# SPDX-License-Identifier: GPL-3.0-only
"""Own synthetic CAD only. Native OCP is a test writer, never a file reader."""
import argparse, json, math, struct
from pathlib import Path


def stl_samples(folder):
    vertices=[(0,0,0),(20,0,0),(20,20,0),(0,20,0),(0,0,20),(20,0,20),(20,20,20),(0,20,20)]
    faces=[(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    lines=['solid synthetic']
    binary=bytearray(b'solid binary header'.ljust(80,b' ')+struct.pack('<I',12))
    for face in faces:
        points=[vertices[i] for i in face]
        lines.extend(['facet normal 0 0 0','outer loop',*['vertex '+' '.join(map(str,p)) for p in points],'endloop','endfacet'])
        binary.extend(struct.pack('<12fH',float('nan'),0,0,*[v for p in points for v in p],0))
    lines.append('endsolid synthetic')
    (folder/'ASCII 立方体.stl').write_text('\n'.join(lines),'ascii')
    (folder/'Binary 法向异常 😀.stl').write_bytes(binary)
    (folder/'空模型.stl').write_text('solid empty\nendsolid empty','ascii')
    (folder/'损坏.stl').write_bytes(b'solid broken\nfacet normal nope')


def generate(folder):
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorType
    from OCP.TDataStd import TDataStd_Name
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.gp import gp_Trsf,gp_Vec
    from OCP.TopLoc import TopLoc_Location
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.Interface import Interface_Static
    from OCP.IFSelect import IFSelect_RetDone
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False);stl_samples(folder)
    manifest={}
    for protocol,filename,complexity,unit in [('AP203','AP203 单实体.step',1,'MM'),('AP214IS','AP214 多实体颜色.stp',2,'MM'),('AP242DIS','AP242 装配81件.step',81,'MM'),('AP242DIS','AP242 英寸单位.step',2,'INCH')]:
        doc=TDocStd_Document(TCollection_ExtendedString('XmlXCAF'))
        tool=XCAFDoc_DocumentTool.ShapeTool_s(doc.Main());colors=XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        assembly=tool.NewShape();TDataStd_Name.Set_s(assembly,TCollection_ExtendedString('Synthetic fixture assembly'))
        def add(shape,name,color,xyz=(0,0,0)):
            label=tool.AddShape(shape,False);TDataStd_Name.Set_s(label,TCollection_ExtendedString(name))
            colors.SetColor(label,Quantity_Color(*color,Quantity_TOC_RGB),XCAFDoc_ColorType.XCAFDoc_ColorGen)
            transform=gp_Trsf();transform.SetTranslation(gp_Vec(*xyz));tool.AddComponent(assembly,label,TopLoc_Location(transform))
        if complexity<3:
            add(BRepPrimAPI_MakeBox(20,20,10).Shape(),'Base',(0.15,.4,.8))
            if complexity==2:add(BRepPrimAPI_MakeCylinder(5,20).Shape(),'Pin',(.8,.2,.1),(10,10,10))
        else:
            add(BRepPrimAPI_MakeBox(130,100,8).Shape(),'Plate',(.2,.45,.8))
            for y in range(8):
                for x in range(8):add(BRepPrimAPI_MakeCylinder(2.5,14).Shape(),f'Bolt_{y}_{x}',(.7,.72,.76),(12+x*15,10+y*11,8))
            sleeve=BRepAlgoAPI_Cut(BRepPrimAPI_MakeCylinder(5,8).Shape(),BRepPrimAPI_MakeCylinder(3,8).Shape()).Shape()
            for y in range(4):
                for x in range(4):add(sleeve,f'Sleeve_{y}_{x}',(.8,.5,.1),(20+x*30,20+y*20,8))
        tool.UpdateAssemblies();writer=STEPCAFControl_Writer()
        assert Interface_Static.SetCVal_s('write.step.schema',protocol)
        assert Interface_Static.SetCVal_s('write.step.unit',unit)
        assert writer.Transfer(doc)
        path=folder/filename;assert writer.Write(str(path))==IFSelect_RetDone
        manifest[filename]=dict(parts=complexity,protocol=protocol,unit=unit,bytes=path.stat().st_size)
    (folder/'损坏.step').write_text('ISO-10303-21;\nnot a STEP;','ascii')
    (folder/'samples.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path)
    print(json.dumps(generate(parser.parse_args().output),ensure_ascii=True))
