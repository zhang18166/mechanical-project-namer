# SPDX-License-Identifier: GPL-3.0-only
"""Ephemeral account session; no desktop or password-hashing dependency."""
import http.client
import json
from .publish_protocol import data,uid,ProtocolError,MAX_JSON
from .publish_client import Client


class AccountSession:
    def __init__(self,config):
        if set(config)!={'port','service'} or type(config['port']) is not int or not 1<=config['port']<=65535:raise ProtocolError('connection-config')
        uid(config['service']);self.config=dict(config);self.token=None;self.user=None

    def request(self,method,route,value=None,*,authenticated=True):
        if authenticated:
            if not self.token:raise ProtocolError('login-required')
            self.verify_service()
        c=http.client.HTTPConnection('127.0.0.1',self.config['port'],timeout=10)
        try:
            headers={}
            if authenticated:
                if not self.token:raise ProtocolError('login-required')
                headers['Authorization']='Bearer '+self.token
            c.request(method,route,body=data(value) if value is not None else None,headers=headers)
            r=c.getresponse();b=r.read(MAX_JSON+1)
            if len(b)>MAX_JSON:raise ProtocolError('response-limit')
            if r.status!=200:
                raise ProtocolError('account-request-rejected-'+str(r.status))
            return json.loads(b)
        finally:c.close()

    def verify_service(self):
        v=self.request('GET','/health',authenticated=False)
        if v.get('service')!=self.config['service'] or v.get('accounts') is not True:raise ProtocolError('service-identity')
        return v

    def login(self,name,password):
        self.token=None;self.user=None
        self.verify_service()
        v=self.request('POST','/auth/login',dict(username=name,password=password),authenticated=False)
        if v['service']!=self.config['service']:raise ProtocolError('service-identity')
        self.token=v['token'];self.user=v['user']
        return dict(user=self.user,expires_at=v['expires_at'])

    def me(self):
        v=self.request('GET','/auth/me');self.user=v['user'];return v

    def logout(self):
        try:
            if self.token:self.request('POST','/auth/logout',{})
        finally:self.token=None;self.user=None

    def change_password(self,old,new):
        result=self.request('POST','/auth/password',dict(old_password=old,new_password=new))
        self.token=None;self.user=None;return result

    def projects(self):return self.request('GET','/projects')['projects']

    def publisher(self,project):
        if not self.token:raise ProtocolError('login-required')
        return Client(dict(self.config,project=uid(project),token=self.token))

    def versions(self,project):return self.publisher(project).versions()
