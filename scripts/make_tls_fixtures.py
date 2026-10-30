# SPDX-License-Identifier: GPL-3.0-only
"""Generate private, explicitly test-only CA/certificates outside any Git tree."""
import argparse
import datetime as dt
import ipaddress
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from mechanical_project_namer.account_server import external


def generate(root):
    root=external(root)
    root.mkdir(parents=True,mode=0o700,exist_ok=False)
    now=dt.datetime.now(dt.timezone.utc)
    key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Workbench isolated TEST CA')])
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(days=1))
        .not_valid_after(now+dt.timedelta(days=7)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),critical=True)
        .sign(key,hashes.SHA256()))
    def put(name,b):
        with (root/name).open('xb') as f:f.write(b)
        (root/name).chmod(0o600)
    put('ca.pem',ca.public_bytes(serialization.Encoding.PEM))
    put('ca.key',key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    for label,start,end,host in [('valid',-1,2,'localhost'),('replacement',-1,3,'localhost'),
                                 ('wrong-host',-1,2,'wrong.invalid'),('expired',-3,-2,'localhost'),('future',2,3,'localhost')]:
        k=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        sans=[x509.DNSName(host)]
        if host=='localhost':sans.append(x509.IPAddress(ipaddress.ip_address('127.0.0.1')))
        cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,host)]))
              .issuer_name(name).public_key(k.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now+dt.timedelta(days=start)).not_valid_after(now+dt.timedelta(days=end))
              .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
              .add_extension(x509.SubjectAlternativeName(sans),critical=False)
              .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
              .sign(key,hashes.SHA256()))
        put(label+'.pem',cert.public_bytes(serialization.Encoding.PEM))
        put(label+'.key',k.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    return root


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    generate(p.parse_args().output)
    print('isolated-test-certificates-created')
