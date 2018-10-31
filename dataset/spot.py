import os

import numpy as np
import pandas as pd
from pandas.tseries.offsets import Day
import statsmodels.api as sm

import torch
from torch.utils.data import Dataset, DataLoader

DIR = os.path.dirname(__file__)

SPOT = pd.read_csv(os.path.join(DIR, 'data/simple_spot.csv'),
                   index_col='datetime', parse_dates=True).iloc[:, 0]

DAY = pd.read_csv(os.path.join(DIR, 'seasonal/day.csv'),
                   index_col='datetime', parse_dates=True) # no mean & std

WEEK = pd.read_csv(os.path.join(DIR, 'seasonal/week.csv'),
                   index_col='datetime', parse_dates=True) # no mean & std

TOTAL_STD = 22.732
TOTAL_MEAN = 38.99

# HOLIDAY = pd.read_csv(os.path.join(DIR, 'simple_holiday.csv'),
#                       index_col='date', parse_dates=True).iloc[:, 0]

cared = SPOT['2012':'2015']
hour_mean = cared.groupby(cared.index.hour).mean()
hour_std = cared.groupby(cared.index.hour).std()

# SPOT = (SPOT - SPOT.index.map(lambda x: hour_mean[x.hour])) /\
#     SPOT.index.map(lambda x: hour_std[x.hour])
# SPOT = (SPOT - SPOT.index.map(lambda x: hour_mean[x.hour])) / 22.38


def normalize(x):
    return (x - TOTAL_MEAN) / TOTAL_STD


def reduce_hour_mean():
    return (SPOT - SPOT.index.map(lambda x: hour_mean[x.hour])) / TOTAL_STD


def to_stardard(x):
    return x * TOTAL_STD + hour_mean.values  # broadcast


def get_daily_spot(data=SPOT):
    reshaped = pd.DataFrame()
    for i in range(24):
        reshaped['hour' + str(i)] = data[data.index.hour == i].values
    reshaped.index = pd.to_datetime(data[data.index.hour == i].index.date)
    reshaped.index.name = 'date'
    return reshaped


def get_decomposed_spot(data=SPOT):
    daily_spot = get_daily_spot(data)
    trends = dict()
    seasonals = dict()
    residuals = dict()
    for key, col in daily_spot.T.iterrows():
        res = sm.tsa.seasonal_decompose(col, freq=7)
        trends[key] = res.trend
        seasonals[key] = res.seasonal
        residuals[key] = res.resid
    return (pd.DataFrame(trends)[['hour%s' % i for i in range(24)]],
            pd.DataFrame(seasonals)[['hour%s' % i for i in range(24)]],
            pd.DataFrame(residuals)[['hour%s' % i for i in range(24)]])


def get_loader(dataset, batch_size=64, shuffle=True, num_workers=2):
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=shuffle, num_workers=num_workers)
    return loader


class Holiday:

    def __init__(self, path):
        with open(path) as f:
            start = f.readline()
            assert start.startswith('START: ')
            self.start = pd.to_datetime(start[7:]).date()
            end = f.readline()
            assert end.startswith('END: ')
            self.end = pd.to_datetime(end[5:]).date()
            _ = f.readline()  # skip
            self.core = set()
            for line in f:
                self.core.add(pd.to_datetime(line).date())

    def query(self, date):
        dt = pd.to_datetime(date).date()
        assert dt >= self.start
        assert dt < self.end
        return dt in self.core


HOLIDAY = Holiday(os.path.join(DIR, 'data/holiday.txt'))


class SlidingWindow:

    '''
    end_date: the last day for training
    N: training set size
    W: sliding window size for 1 step
    '''

    def __init__(self, end_date='2015-12-31', N=1200, W=7, W_forward=1):
        self.W = W
        self.W_forward = W_forward
        dt = pd.to_datetime(end_date)
        X = []
        Y = []
        for _ in range(N):
            x = self.get_features(dt)
            y = self.get_output(dt)
            dt -= Day(1)
            X.append(x)
            Y.append(y)
        self.X = np.array(X)
        self.Y = np.array(Y)

    def get_lagged_price(self, date, return_values=True):
        lagged = SPOT[pd.to_datetime(date) - Day(self.W):pd.to_datetime(date)]
        if return_values:
            lagged = lagged.values
        return lagged

    def get_holiday(self, date):
        return HOLIDAY.query(date)

    def get_seasonal(self, date, ohe=True):
        weekday = pd.to_datetime(date).weekday()
        if ohe:
            ret = [0] * 7
            ret[weekday] = 1
            return ret
        return weekday

    def get_features(self, date, price=None):
        if price is None:
            price = self.get_lagged_price(date)
        holiday = self.get_holiday(date)
        seasonal = self.get_seasonal(date)
        # seasonal = []
        return np.array(list(price) + [holiday] + list(seasonal))
        # return np.array(list(price) + list(seasonal))

    def get_output(self, date, return_values=True):
        date = pd.to_datetime(date)
        ret = SPOT[date:date + Day(self.W_forward)]
        if return_values:
            ret = ret.values
        return ret

    def update_training(self, date):
        x = self.get_features(date)
        y = self.get_output(date)
        self.X = np.array([x] + list(self.X[:-1, :]))
        self.Y = np.array([y] + list(self.Y[:-1, :]))


class DailyDataset(Dataset):
    '''
    TODO: clean the dummy ops.
    '''

    def __init__(self, end_date='2015-12-31', N=1200, W=14, USE_DAY=False, USE_WEEK=False):
        self.W = W
        self.USE_DAY = USE_DAY
        self.USE_WEEK = USE_WEEK
        self.spot = get_daily_spot(reduce_hour_mean())
        # self.spot = get_daily_spot(SPOT.diff(24))


        dt = pd.to_datetime(end_date)
        X = []
        Y = []
        dates = []
        for _ in range(N):
            x, y = self._sliding_window(dt)
            X.append(x)
            Y.append(y)
            dates.append(dt)
            dt -= Day(1)

        start_date = dates[-1]

        for _ in range(W):
            dates.append(dt)
            dt -= Day(1)

        self.X = np.array(list(reversed(X)))
        self.Y = np.array(list(reversed(Y)))
        self.dates = np.array(list(reversed(dates)))

        print("Data build range: [window(%s) - %s, %s]" %
              (self.dates[0], start_date, self.dates[-1]))

    @property
    def step_size(self):
        return len(self.dates)  # N + W

    # @property
    # def training_data(self):
    #     return self.get_io(self.dates[0], self.dates[-1])

    # @property
    # def testing_data(self):
    #     '2016-01-01'
    #     '2017-06-30'
    #     pass

    def _sliding_window(self, date):
        _sliding = self.spot.loc[pd.to_datetime(date) - Day(self.W):
                                 pd.to_datetime(date)].values

        y = _sliding[-self.W:]
        if self.USE_DAY:
            d_sliding = DAY.loc[pd.to_datetime(date) - Day(self.W):
                                 pd.to_datetime(date)].values
            _sliding = np.concatenate((_sliding, d_sliding), axis=-1)
        if self.USE_WEEK:
            w_sliding = WEEK.loc[pd.to_datetime(date) - Day(self.W):
                                 pd.to_datetime(date)].values
            _sliding = np.concatenate((_sliding, w_sliding), axis=-1)
        x = _sliding[:self.W]

        return x, y


    def get_io(self, start_date, end_date):
        _sliding = self.spot.loc[pd.to_datetime(start_date) - Day(self.W):
                                 pd.to_datetime(end_date)].values

        o_stream = _sliding[1:]
        if self.USE_DAY:
            d_sliding = DAY.loc[pd.to_datetime(start_date) - Day(self.W):
                                 pd.to_datetime(end_date)].values
            _sliding = np.concatenate((_sliding, d_sliding), axis=-1)
        if self.USE_WEEK:
            w_sliding = WEEK.loc[pd.to_datetime(start_date) - Day(self.W):
                                 pd.to_datetime(end_date)].values
            _sliding = np.concatenate((_sliding, w_sliding), axis=-1)
        i_stream = _sliding[:-1]
        return _sliding, torch.from_numpy(i_stream).float(), torch.from_numpy(o_stream).float()

    def __getitem__(self, item):
        x = torch.from_numpy(self.X[item]).float()
        y = torch.from_numpy(self.Y[item]).float().clamp(-2, 5)
        return x, y, item

    def __len__(self):
        return len(self.X)

    # @classmethod
    # def get_loader(cls, batch_size=64, shuffle=True, *args, **kwargs):
    #     dataset = cls(*args, **kwargs)
    #     loader = DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)
    #     return loader


class PeriodDataset(Dataset):
    '''
    TODO: clean the dummy ops.
    '''

    def __init__(self, end_date='2015-12-31', N=1200, W=14, P=2, USE_DAY=False, USE_WEEK=False):
        self.W = W
        self.P = P
        self.USE_DAY = USE_DAY
        self.USE_WEEK = USE_WEEK
        self.spot = get_daily_spot(reduce_hour_mean())
        # self.spot = get_daily_spot(SPOT.diff(24))

        dt = pd.to_datetime(end_date)
        X = []
        Y = []
        dates = []
        for i in range(N):
            # print(dt, i)
            x, y = self._sliding_window(dt)
            X.append(x)
            Y.append(y)
            dates.append(dt)
            dt -= Day(1)

        start_date = dates[-1]

        for _ in range(W + P - 1):
            dates.append(dt)
            dt -= Day(1)

        self.X = np.array(list(reversed(X)))
        self.Y = np.array(list(reversed(Y)))
        self.dates = np.array(list(reversed(dates)))

        print("Data build range: [window(%s) - %s, %s]" %
              (self.dates[0], start_date, self.dates[-1]))

    @property
    def step_size(self):
        return len(self.dates)  # N + W

    # @property
    # def training_data(self):
    #     return self.get_io(self.dates[0], self.dates[-1])

    # @property
    # def testing_data(self):
    #     '2016-01-01'
    #     '2017-06-30'
    #     pass

    def _sliding_window(self, date):
        _sliding = self.spot.loc[pd.to_datetime(date) - Day(self.W + self.P - 1):
                                 pd.to_datetime(date)].values
        
        y = _sliding[-self.W:]
        if self.USE_DAY:
            d_sliding = DAY.loc[pd.to_datetime(date) - Day(self.W + self.P - 1):
                                 pd.to_datetime(date)].values
            _sliding = np.concatenate((_sliding, d_sliding), axis=-1)
        if self.USE_WEEK:
            w_sliding = WEEK.loc[pd.to_datetime(date) - Day(self.W + self.P - 1):
                                 pd.to_datetime(date)].values
            _sliding = np.concatenate((_sliding, w_sliding), axis=-1)

        x = []
        m = 1 + self.USE_DAY + self.USE_WEEK
        for i in range(self.W):
            x += [_sliding[i:i + self.P].reshape(24 * m * self.P)]
        x = np.array(x)
        
        return x, y

    def get_io(self, start_date, end_date):
        _sliding = self.spot.loc[pd.to_datetime(start_date) - Day(self.W + self.P - 1):
                                 pd.to_datetime(end_date)].values
        o_stream = _sliding[self.P:]
        if pd.to_datetime(end_date) > pd.to_datetime('2016-06-30'):
            _sliding = self.spot.loc[pd.to_datetime(start_date) - Day(self.W + self.P - 1):
                                        pd.to_datetime('2016-06-30')].values

        if self.USE_DAY:
            d_sliding = DAY.loc[pd.to_datetime(start_date) - Day(self.W + self.P - 1):
                                 pd.to_datetime(end_date)].values
            _sliding = np.concatenate((_sliding, d_sliding), axis=-1)
        if self.USE_WEEK:
            w_sliding = WEEK.loc[pd.to_datetime(start_date) - Day(self.W + self.P - 1):
                                 pd.to_datetime(end_date)].values
            _sliding = np.concatenate((_sliding, w_sliding), axis=-1)
        i_stream = []
        m = 1 + self.USE_DAY + self.USE_WEEK
        for i in range(_sliding.shape[0] - self.P):
            i_stream += [_sliding[i:i + self.P].reshape(24 * m * self.P)]
        i_stream = np.array(i_stream)
        
        return torch.from_numpy(i_stream).float(), torch.from_numpy(o_stream).float()

    def __getitem__(self, item):
        x = torch.from_numpy(self.X[item]).float()
        y = torch.from_numpy(self.Y[item]).float().clamp(-2, 5)
        return x, y, item

    def __len__(self):
        return len(self.X)


class SpotDataset(Dataset):

    def __init__(self, end_date='2015-12-31', N=1200, W=14, segment=24):
        self.W = W
        self.segment = segment

        dt = pd.to_datetime(end_date)
        X = []
        Y = []
        dates = []
        seasonal = []
        holiday = []
        for _ in range(N):
            x, y, s, h = self._sliding_window(dt)
            X.append(x)
            Y.append(y)
            seasonal.append(s)
            holiday.append(h)
            dates.append(dt)
            dt -= Day(1)

        self.X = np.array(list(reversed(X)))
        self.Y = np.array(list(reversed(Y)))
        self.dates = np.array(list(reversed(dates)))
        self.seasonal = np.array(list(reversed(seasonal)))
        self.holiday = np.array(list(reversed(holiday)))
        self.step_size = N + W

        print("Data build range:", [self.dates[0], self.dates[-1]])

    def _sliding_window(self, date):
        _sliding = SPOT[pd.to_datetime(date) - Day(self.W):
                        pd.to_datetime(date) + Day(1)]  # TODO
        sliding = _sliding.values.reshape(-1, self.segment)
        timestamps = _sliding.index
        weekdays = np.array([d.weekday()
                             for d in timestamps]).reshape(-1, self.segment)
        holidays = np.array([int(HOLIDAY.query(d))
                             for d in timestamps]).reshape(-1, self.segment)
        return (sliding[:self.W, :], sliding[-self.W:, :],
                weekdays[-self.W:, 0], holidays[-self.W:, 0])

    def __getitem__(self, item):
        x = torch.from_numpy(self.X[item]).float()
        y = torch.from_numpy(self.Y[item]).float()
        # seasonal flag of predict step
        seasonal = torch.from_numpy(self.seasonal[item])
        # holiday flag of predict step
        holiday = torch.from_numpy(self.holiday[item])
        return x, y, seasonal, holiday, item

    def __len__(self):
        return len(self.X)

    @classmethod
    def get_loader(cls, batch_size=64, shuffle=True, *args, **kwargs):
        dataset = cls(*args, **kwargs)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)
        return loader
