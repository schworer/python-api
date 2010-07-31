#!/usr/bin/env python

"""
Python Shotgun API library.

docs and latest version available for download at
 https://support.shotgunsoftware.com/forums/48807-developer-api-info
"""

__version__ = "3.0.1"

import cookielib
import os
import urllib2
import sys
from urlparse import urlparse

from lib.form_post_handler import FormPostHandler
from lib.xmlrpc_sg import ServerProxy, ProxiedTransport, Fault

class ShotgunError(Exception): pass

class Shotgun(object):
    # Used to split up requests into batches of records_per_page when doing 
    # requests.  this helps speed tremendously when getting lots of results
    # back.  doesn't affect the interface of the api at all (you always get the
    # full set of results back as one array) but just how the client class 
    # communicates with the server.
    records_per_page = 500

    def __init__(self, base_url, script_name, api_key, convert_datetimes_to_utc=True, http_proxy=None):
        """
        Initialize Shotgun.
        """
        self.server = None
        if base_url.split("/")[0] not in ("http:","https:"):
            raise ShotgunError("URL protocol must be http or https.  Value was '%s'" % base_url)

        # cheesy way to strip off anything past the domain name, so:
        # http://blah.com/asd => http://blah.com
        self.base_url = "/".join(base_url.split("/")[0:3]) 
        self.script_name = script_name
        self.api_key = api_key

        # keep using api3_preview to be compatible with older servers
        self.api_ver = 'api3_preview' 

        self.api_url = "%s/%s/" % (self.base_url, self.api_ver)
        self.convert_datetimes_to_utc = convert_datetimes_to_utc
        self.sid = None # only load this if needed
        self.http_proxy = http_proxy
        
        server_options = {
            'server_url': self.api_url,
            'script_name': self.script_name,
            'script_key': self.api_key,
            'http_proxy' : self.http_proxy,
            'convert_datetimes_to_utc': self.convert_datetimes_to_utc
        }
        
        self._api3 = ShotgunCRUD(server_options)
        
    def _get_thumb_url(self, entity_type, entity_id):
        """
        Returns the URL for the thumbnail of an entity given the 
        entity type and the entity id 
        """
        url = self.base_url + "/upload/get_thumbnail_url?entity_type=%s&entity_id=%d"%(entity_type,entity_id)
        for i in range(3):
            f = urllib2.urlopen(url)
            response_code = f.readline().strip()
            # something else happened. try again. found occasional connection errors still spit out html but not
            # the correct response codes. usually trying again will right the ship. if not, we catch for it later.
            if response_code not in ('0','1'): 
                continue    
            elif response_code == '1': 
                path = f.readline().strip()
                if path:
                    return self.base_url + path
            elif response_code == '0':
                break                        
        # if it's an error, message is printed on second line
        raise ValueError("%s:%s " % (entity_type,entity_id)+f.read().strip())
    
    def schema_read(self):
        resp = self._api3.schema_read()
        return resp["results"]
    
    def schema_field_read(self, entity_type, field_name=None):
        args = {
            "type":entity_type
        }
        if field_name:
            args["field_name"] = field_name
        resp = self._api3.schema_field_read(args)
        return resp["results"]
    
    def schema_field_create(self, entity_type, data_type, display_name, properties=None):
        if properties == None: 
            properties = {}
        
        args = {
            "type":entity_type,
            "data_type":data_type,
            "properties":[{'property_name': 'name', 'value': display_name}]
        }
        for f,v in properties.items():
            args["properties"].append( {"property_name":f,"value":v} )
        resp = self._api3.schema_field_create(args)
        return resp["results"]
    
    def schema_field_update(self, entity_type, field_name, properties):
        args = {
            "type":entity_type,
            "field_name":field_name,
            "properties":[]
        }
        for f,v in properties.items():
            args["properties"].append( {"property_name":f,"value":v} )
        resp = self._api3.schema_field_update(args)
        return resp["results"]
    
    def schema_field_delete(self, entity_type, field_name):
        args = {
            "type":entity_type,
            "field_name":field_name
        }
        resp = self._api3.schema_field_delete(args)
        return resp["results"]
    
    def schema_entity_read(self):
        resp = self._api3.schema_entity_read()
        return resp["results"]

    def find(self, entity_type, filters, fields=None, order=None, filter_operator=None, limit=0, retired_only=False):
        """
        Find entities of entity_type matching the given filters.
        
        The columns returned for each entity match the 'fields' 
        parameter provided, or just the id if nothing is specified.
        
        Limit constrains the total results to its value.
        
        Returns an array of dict entities sorted by the optional
        'order' parameter. 
        """
        if fields == None: 
            fields = ['id']
        if order == None: 
            order = []
        
        # we want to check if filters is iterable, not only if it's a list.
        if hasattr(filters, '__iter__'):
            new_filters = {}
            if not filter_operator or filter_operator == "all":
                new_filters["logical_operator"] = "and"
            else:
                new_filters["logical_operator"] = "or"
            
            new_filters["conditions"] = []
            for f in filters:
                new_filters["conditions"].append( {"path":f[0],"relation":f[1],"values":f[2:]} )
            
            filters = new_filters
        elif filter_operator:
            raise ShotgunError("Deprecated: Use of filter_operator for find() is not valid any more.  See the documention on find()")
        
        if retired_only:
            return_only = 'retired'
        else:
            return_only = 'active'
        
        req = {
            "type": entity_type,
            "return_fields": fields,
            "filters": filters,
            "return_only" : return_only,
            "paging": {"entities_per_page": self.records_per_page, "current_page": 1}
        }
        
        if order:
            req['sorts'] = []
            for sort in order:
                if sort.has_key('column'):
                    # TODO: warn about deprecation of 'column' param name
                    sort['field_name'] = sort['column']
                if not sort.has_key('direction'):
                    sort['direction'] = 'asc'
                req['sorts'].append({'field_name': sort['field_name'],
                                     'direction' : sort['direction']})
        
        if (limit and limit > 0 and limit < self.records_per_page):
            req["paging"]["entities_per_page"] = limit
        
        records = []
        done = False
        while not done:
            resp = self._api3.read(req)
            results = resp["results"]["entities"]
            if results:
                records.extend(results)
                if ( len(records) >= limit and limit > 0 ):
                    records = records[:limit]
                    done = True
                elif len(records) == resp["results"]["paging_info"]["entity_count"]:
                    done = True
                else:
                    req['paging']['current_page'] += 1
            else:
                done = True
        
        # 'image' only returns id by default. add links to the thumbnail images
        if 'image' in set(fields):
            for i,v in enumerate(records):
                if records[i]['image']:
                    records[i]['image'] = self._get_thumb_url(entity_type,records[i]['id'])
        
        return records
    
    def find_one(self, entity_type, filters, fields=None, order=None, filter_operator=None, retired_only=False):
        """
        Same as find, but only returns 1 result as a dict 
        """
        result = self.find(entity_type, filters, fields, order, filter_operator, 1, retired_only)
        if len(result) > 0:
            return result[0]
        else:
            return None
    
    def _required_keys(self, message, required_keys, data):
        missing = set(required_keys) - set(data.keys())
        if missing:
            raise ShotgunError("%s missing required key: %s. Value was: %s." % (message, ", ".join(missing), data))
    
    def batch(self, requests):
        if type(requests) != type([]):
            raise ShotgunError("batch() expects a list.  Instead was sent a %s"%type(requests))
        
        reqs = []
        
        for r in requests:
            self._required_keys("Batched request",['request_type','entity_type'],r)
            
            if r["request_type"] == "create":
                self._required_keys("Batched create request",['data'],r)
                    
                nr = {
                    "request_type": "create",
                    "type": r["entity_type"],
                    "fields": []
                }
                
                if "return_fields" in r:
                    nr["return_fields"] = r
                
                for f,v in r["data"].items():
                    nr["fields"].append( { "field_name": f, "value": v } )
                
                reqs.append(nr)
            elif r["request_type"] == "update":
                self._required_keys("Batched create request",['entity_id','data'],r)
                    
                nr = {
                    "request_type": "update",
                    "type": r["entity_type"],
                    "id": r["entity_id"],
                    "fields": []
                }
                
                for f,v in r["data"].items():
                    nr["fields"].append( { "field_name": f, "value": v } )
                
                reqs.append(nr)
            elif r["request_type"] == "delete":
                self._required_keys("Batched delete request",['entity_id'],r)
                    
                nr = {
                    "request_type": "delete",
                    "type": r["entity_type"],
                    "id": r["entity_id"]
                }
                
                reqs.append(nr)
            else:
                raise ShotgunError("Invalid request_type for batch")
        
        resp = self._api3.batch(reqs)
        return resp["results"]
        
    def create(self, entity_type, data, return_fields=None):
        """
        Create a new entity of entity_type type.
        
        'data' is a dict of key=>value pairs of fieldname and value
        to set the field to. 
        """
        if return_fields == None: 
            return_fields = ['id']
        
        args = {
            "type":entity_type,
            "fields":[],
            "return_fields":return_fields
        }
        for f,v in data.items():
            args["fields"].append( {"field_name":f,"value":v} )
        
        resp = self._api3.create(args)
        return resp["results"]
        
    def update(self, entity_type, entity_id, data):
        """
        Update an entity given the entity_type, and entity_id
        
        'data' is a dict of key=>value pairs of fieldname and value
        to set the field to. 
        """
        args = {"type":entity_type,"id":entity_id,"fields":[]}
        for f,v in data.items():
            args["fields"].append( {"field_name":f,"value":v} )
            
        resp = self._api3.update(args)
        return resp["results"]

    def delete(self, entity_type, entity_id):
        """
        Retire an entity given the entity_type, and entity_id
        """
        resp = self._api3.delete( {"type":entity_type, "id":entity_id} )
        return resp["results"]

    def upload(self, entity_type, entity_id, path, field_name=None, display_name=None, tag_list=None):
        """
        Upload a file as an attachment/thumbnail to the entity_type and entity_id
        
        @param entity_type: the entity type
        @param entity_id: id for given entity to attach to
        @param path: path to file on disk
        @param field_name: the field on the entity to upload to (ignored if thumbnail)
        @param display_name: the display name to use for the file in the ui (ignored if thumbnail)
        @param tag_list: comma-separated string of tags to assign to the file
        """
        is_thumbnail = (field_name == "thumb_image")
        
        params = {}
        params["entity_type"] = entity_type
        params["entity_id"] = entity_id
        
        # send auth, so server knows which 
        # script uploaded the file
        params["script_name"] = self.script_name
        params["script_key"] = self.api_key
        
        if not os.path.isfile(path):
            raise ShotgunError("Path must be a valid file.")
        
        url = "%s/upload/upload_file" % (self.base_url)
        if is_thumbnail:
            url = "%s/upload/publish_thumbnail" % (self.base_url)
            params["thumb_image"] = open(path, "rb")
        else:
            if display_name is None:
                display_name = os.path.basename(path)
            # we allow linking to nothing for generic reference use cases
            if field_name is not None:
                params["field_name"] = field_name
            params["display_name"] = display_name
            params["tag_list"] = tag_list
            params["file"] = open(path, "rb")

        # Create opener with extended form post support
        opener = urllib2.build_opener(FormPostHandler)
        
        # Perform the request
        try:
            result = opener.open(url, params).read()
        except urllib2.HTTPError, e:
            if e.code == 500:
                raise ShotgunError("Server encountered an internal error. \n%s\n(%s)\n%s\n\n" % (url, params, e))
            else:
                raise ShotgunError("Unanticipated error occurred uploading %s: %s" % (path, e))
        else:
            if not str(result).startswith("1"):
                raise ShotgunError("Could not upload file successfully, but not sure why.\nPath: %s\nUrl: %s\nError: %s" % (path, url, str(result)))
        
        # we changed the result string in the middle of 1.8 to return the id
        # remove once everyone is > 1.8.3
        r = str(result).split(":") 
        id = 0
        if len(r) > 1:
            id = int(str(result).split(":")[1].split("\n")[0])
        return id
        
    def upload_thumbnail(self, entity_type, entity_id, path, **kwargs):
        """
        Convenience function for thumbnail uploads.
        """
        result = self.upload(entity_type, entity_id, path, field_name="thumb_image", **kwargs)
        return result
        
    def download_attachment(self, entity_id):
        """
        Gets session authentication and returns binary content of Attachment data
        """
        sid = self._get_session_token()
        domain = urlparse(self.base_url)[1].split(':',1)[0]
        cj = cookielib.LWPCookieJar()
        c = cookielib.Cookie('0', '_session_id', sid, None, False, domain, False, False, "/", True, False, None, True, None, None, {})
        cj.set_cookie(c)
        cookie_handler = urllib2.HTTPCookieProcessor(cj)
        urllib2.install_opener(urllib2.build_opener(cookie_handler))
        url = '%s/file_serve/attachment/%s' % (self.base_url, entity_id)

        try:
            request = urllib2.Request(url)
            request.add_header('User-agent','Mozilla/5.0 (Macintosh; U; Intel Mac OS X 10.5; en-US; rv:1.9.0.7) Gecko/2009021906 Firefox/3.0.7')
            attachment = urllib2.urlopen(request).read() 

        except IOError, e:
            err = "Failed to open %s" % url
            if hasattr(e, 'code'):
                err += "\nWe failed with error code - %s." % e.code
            elif hasattr(e, 'reason'):
                err += "\nThe error object has the following 'reason' attribute :", e.reason
                err += "\nThis usually means the server doesn't exist, is down, or we don't have an internet connection."
            raise ShotgunError(err)
        else:
            if attachment.lstrip().startswith('<!DOCTYPE HTML'):
                raise ShotgunError("The server generated an error trying to download the Attachment. \nURL: %s\nServer Response: %s" % (url, attachment))
        return attachment
    
    def _get_session_token(self):
        """
        Hack to authenticate in order to download protected content
        like Attachments
        """
        if self.sid == None:
            # HACK: use API2 to get token for now until we better resolve how we manage Attachments in general
            api2_url = "%s/%s/" % (self.base_url, 'api2')
            conn = ServerProxy(api2_url)
            self.sid = conn.getSessionToken([self.script_name, self.api_key])['session_id']
        return self.sid

    # Deprecated methods from old wrapper
    def schema(self, entity_type):
        raise ShotgunError("Deprecated: use schema_field_read('type':'%s') instead" % entity_type)
    
    def entity_types(self):
        raise ShotgunError("Deprecated: use schema_entity_read() instead")

class ShotgunCRUD(object):
    def __init__(self, options):
        self.__sg_url = options['server_url']
        self.__auth_args = {'script_name': options['script_name'], 'script_key': options['script_key']}
        if 'convert_datetimes_to_utc' in options:
            convert_datetimes_to_utc = options['convert_datetimes_to_utc']
        else:
            convert_datetimes_to_utc = 1
        if 'error_stream' in options:
            self.__err_stream = options['error_stream']
        else:
            self.__err_stream = sys.stderr
        if 'http_proxy' in options and options['http_proxy']:
            p = ProxiedTransport()
            p.set_proxy( options['http_proxy'] )
            self.__sg = ServerProxy(self.__sg_url, convert_datetimes_to_utc = convert_datetimes_to_utc, transport=p)
        else:
            self.__sg = ServerProxy(self.__sg_url, convert_datetimes_to_utc = convert_datetimes_to_utc)
    
    def __getattr__(self, attr):
        def callable(*args, **kwargs):
            return self.meta_caller(attr, *args, **kwargs)
        return callable
    
    def meta_caller(self, attr, *args, **kwargs):
        try:

            # attempt to get the remote call from the Proxy Server
            rpc_func = getattr(self.__sg, attr, None)
            if rpc_func:
                return rpc_func(self.__auth_args, *args, **kwargs)
            else:
                raise ShotgunError('No attribute %s on rpc server' % attr)
        except Fault, e:
            if self.__err_stream:
                self.__err_stream.write("\\n" + "-"*80 + "\\n")
                self.__err_stream.write("XMLRPC Fault %s: \\n" % e.faultCode)
                self.__err_stream.write(e.faultString)
                self.__err_stream.write("\\n" + "-"*80 + "\\n")
            raise e

if __name__ == "__main__":
    api_key = 'ca8e878c9c7f6d8ab3bf1d92fd1a624361cf4e6e'
    sg = Shotgun('http://localhost:3000', 'wrapper_script', api_key)

    # from pprint import pprint
    # for i in range(1001,5000):
    #     pprint(sg.create("Asset",{"code":"Asset %d"%i,"project_names":"Test Project"}))
    # 
    # pprint(sg.find("Asset",filters=[], filter_operator='any', fields=['code','image']))
    # sg.upload("Asset", 1, "/path/to/file.png", display_name="My File", field_name="sg_attachment")
