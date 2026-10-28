# SPDX-License-Identifier: GPL-3.0-only
"""Finite, memory-limited CAD lexical/triangle preprocessing. No native geometry import."""
import base64,json,os,sys
from threading import Event
from .cad import encode_stl,step_preflight,CadSnapshot
from .pdf_worker import receive,send


if __name__=='__main__':
    try:
        request=json.loads(receive());data=receive()
        if request['extension']=='.stl':model=encode_stl(data,Event())
        elif request['extension'] in ('.step','.stp'):model=CadSnapshot(data,0,len(data),'step',step_preflight(data,Event()))
        else:raise ValueError('不支持此CAD格式。')
        send(dict(pid=os.getpid(),data=base64.b64encode(model.data).decode('ascii'),triangles=model.triangles,format=model.format,metadata=model.metadata))
    except Exception as exc:
        send(dict(error=str(exc)));sys.exit(1)
