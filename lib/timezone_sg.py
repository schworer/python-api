#!/usr/bin/env python

#  SG_TIMEZONE module 

from datetime import tzinfo, timedelta
import time as _time

ZERO = timedelta(0)
STDOFFSET = timedelta(seconds = -_time.timezone)
if _time.daylight:
    DSTOFFSET = timedelta(seconds = -_time.altzone)
else:
    DSTOFFSET = STDOFFSET
DSTDIFF = DSTOFFSET - STDOFFSET

class SgTimezone:
    def __init__(self):
        self.utc = self.UTC()
        self.local = self.LocalTimezone()
        
    class UTC(tzinfo):
        def utcoffset(self, dt):
            return ZERO

        def tzname(self, dt):
            return "UTC"

        def dst(self, dt):
            return ZERO

    class LocalTimezone(tzinfo):
        def utcoffset(self, dt):
            if self._isdst(dt):
                return DSTOFFSET
            else:
                return STDOFFSET

        def dst(self, dt):
            if self._isdst(dt):
                return DSTDIFF
            else:
                return ZERO

        def tzname(self, dt):
            return _time.tzname[self._isdst(dt)]

        def _isdst(self, dt):
            tt = (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.weekday(), 0, -1)
            stamp = _time.mktime(tt)
            tt = _time.localtime(stamp)
            return tt.tm_isdst > 0

sg_timezone = SgTimezone()

