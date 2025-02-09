#
# Copyright (C) 2008 Henry Zhou <jiangwen365@gmail.com>
# Copyright (C) 2001 Thierry MICHEL <thierry@nekhem.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
#


import six
import pyodbc
import locale
import os
import sys
import datetime
from DateTime import DateTime
from Shared.DC.ZRDB.TM import TM

if sys.version_info[1] <= 4:
    pyodbc_dir = os.path.dirname(os.path.realpath(__file__)) + '\\pyodbc24'
else:
    pyodbc_dir = os.path.dirname(os.path.realpath(__file__)) + '\\pyodbc26'
sys.path.append(pyodbc_dir)

# Patch JH 08/2016: Decimals are formatted according to the locale set
# at "import" time.
orig_locale = locale.getlocale(locale.LC_NUMERIC)
locale.setlocale(locale.LC_NUMERIC, 'C')
# End of Patch JH 08/2016


# Patch JH 08/2016: Restore original locale.
locale.setlocale(locale.LC_NUMERIC, orig_locale)
# End of Patch JH 08/2016

sys.path.remove(pyodbc_dir)

DB_Error = pyodbc.Error


def convert9(value):
    # Data Type 9, not recognized by ODBC
    # is a Null
    return None


class DB(TM):
    """DB class from pyodbc driver"""

    def __init__(self, po_conx):
        conn_param = eval(po_conx)
        self._conxString = conn_param['connx_string']
        self._auto_commit = conn_param['auto_commit']
        self._MaxRows = conn_param['MaxRows']

        self._conx = pyodbc.connect(self._conxString,
                                    autocommit=self._auto_commit)
        # Here we add a new data type converter
        self._conx.add_output_converter(9, convert9)

        self._cursor = self._conx.cursor
        self._numTry = 0
        self._numMaxTry = 5

    def setAutocommit(self, auto_commit=None):
        self._auto_commit = auto_commit

    def setMaxRows(self, MaxRows):
        try:
            self._MaxRows = int(MaxRows)
        except ValueError as mesg:
            raise mesg

    def connect(self, ps_queryString):
        from time import sleep
        mesg = ''
        while (self._numTry < self._numMaxTry):
            self._numTry = self._numTry + 1
            try:
                self._conx = pyodbc.connect(self._conxString,
                                            self._auto_commit)
                self._cursor = self._conx.cursor
                return self.query(ps_queryString)
            except pyodbc.OperationalError as err:
                mesg = err
                sleep(5)
        self._numTry = 0
        raise mesg

    def close(self):
        pass

    def query(self, ps_queryString, pl_maxRows=None):
        self._register()
        # This seems not necessary and sometimes breaks certain SQLs.
        # ps_queryString = ps_queryString.replace("\n"," ")
        # pyodbc version 4 needs to receive unicode, not bytes.
        if (pyodbc.version.split('.')[0] == '4' and
                isinstance(ps_queryString, bytes)):
            ps_queryString = ps_queryString.decode('utf-8')
        try:
            o_cur = self._cursor()
            o_cur.execute(ps_queryString)
            # Occasionally the connection is lost which I haven't figured
            # out why, but this re-connecting works for me very well.
            # Need to improve though!
        except pyodbc.Error:
            try:
                o_cur.close()
                self._conx.close()
            except Exception:
                pass
            self._conx = pyodbc.connect(self._conxString,
                                        autocommit=self._auto_commit)
            self._cursor = self._conx.cursor
            o_cur = self._cursor()
            o_cur.execute(ps_queryString)

        try:
            while True:
                o_desc = o_cur.description

                if o_desc:
                    max_rows = (pl_maxRows if pl_maxRows is not None
                                else self._MaxRows)
                    if max_rows:
                        o_result = o_cur.fetchmany(max_rows)
                        # Do not truncate a query result if it exceeds the
                        # maximum row number set in either the connector or
                        # query properties.
                        if len(o_result) == max_rows:
                            try:
                                overshoot_result = o_cur.fetchone()
                            except:
                                overshoot_result = None
                            if overshoot_result:
                                assert False, (
                                    "This query has returned more than "
                                    "MaxRows results. Please raise "
                                    "MaxRows or limit in SQL.")
                    else:
                        o_result = o_cur.fetchall()
                if not o_cur.nextset():
                    break
        finally:
            o_cur.close()

        if o_desc is None:
            return (), ()

        o_items = [
            {
                'name': x[0],
                'type': x[1],
                'dsize': x[2],
                'isize': x[3],
                'precision': x[4],
                'scale': x[5],
                'null': x[6]
            } for x in o_desc
        ]

        field_types = [i['type'] for i in o_items]

        # get date field ids to be convert to DateTime type
        date_field_ids = [i for i in range(len(field_types))
                          if field_types[i] in (datetime.datetime,
                                                datetime.date)
                          ]
        # get float field ids to be convert from decimal to float
        float_field_ids = [i for i in range(len(field_types))
                           if field_types[i] in (float,)
                           ]

        # JJ: Make sure all names are strings (utf-8 encoded)
        def to_string(val):
            if six.PY2 and isinstance(val, six.text_type):
                return val.encode('utf-8')
            return val

        for item in o_items:
            item['name'] = to_string(item['name'])

        # if any pyodbc's object type needs to be converted to
        # Zope's object type.
        if len(date_field_ids) + len(float_field_ids) > 0:
            for row in o_result:
                for field_id in date_field_ids:
                    if row[field_id] is not None:
                        # Currently we don't do timezones. Everything is UTC.
                        # Ideally we'd get the current Oracle timezone
                        # and use that.
                        row[field_id] = DateTime(
                            *(row[field_id].timetuple()[:6] + ('UTC',)))
                for field_id in float_field_ids:
                    if row[field_id] is not None:
                        row[field_id] = float(row[field_id])

        return o_items, o_result

    def _datetime_convert(self, dt, val):
        if dt and (val is not None):
            # Currently we don't do timezones. Everything is UTC.
            # Ideally we'd get the current Oracle timezone and use that.
            x = val.timetuple()[:6] + ('UTC',)
            return DateTime(*x)
        return val

    def _Decimal_convert(self, dt, val):
        if dt and (val is not None):
            return float(val)
        return val

    def getdbinfo(self):
        dbinfo = ''
        try:
            dbinfo += self._conx.getinfo(pyodbc.SQL_DBMS_NAME) + ' '
            dbinfo += self._conx.getinfo(pyodbc.SQL_DBMS_VER) + ' '
            dbinfo += self._conx.getinfo(pyodbc.SQL_DATABASE_NAME) + ' '
        except Exception:
            pass
        return dbinfo

    def get_columns(self, table_name):
        o_cur = self._cursor()
        columns = [column for column in o_cur.columns(table_name)]
        o_cur.close()
        return columns

    def get_tables(self):
        o_cur = self._cursor()
        tables = [table for table in o_cur.tables()]
        o_cur.close()
        return tables

    def get_primaryKeys(self, table_name):
        o_cur = self._cursor()
        primaryKeys = []
        try:
            primaryKeys = [p_key for p_key in o_cur.primaryKeys(table_name)]
        except Exception:
            pass
        o_cur.close()
        return primaryKeys

    def get_foreignKeys(self, table_name):
        o_cur = self._cursor()
        foreignKeys = []
        try:
            foreignKeys = [f_key for f_key in o_cur.foreignKeys(
                foreignTable=table_name)]
        except Exception:
            pass
        o_cur.close()
        return foreignKeys

    def _finish(self):
        try:
            self._conx.commit()
        except DB_Error:
            raise

    def _abort(self):
        try:
            self._conx.rollback()
        except DB_Error:
            try:
                self._conx.close()
            except DB_Error:
                # Due to T249828 we catch error that occurs during connection closing and ignore it.
                pass

    def _begin(self):
        pass
