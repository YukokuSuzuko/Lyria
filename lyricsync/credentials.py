"""Windows Credential Manager only; never a plaintext settings fallback.

ABI: https://learn.microsoft.com/windows/win32/api/wincred/ns-wincred-credentialw
"""
import ctypes as c
from ctypes import wintypes as w
import hashlib
import os


class Credential(c.Structure):
    _fields_=[('Flags',w.DWORD),('Type',w.DWORD),('TargetName',w.LPWSTR),
              ('Comment',w.LPWSTR),('LastWritten',w.FILETIME),('CredentialBlobSize',w.DWORD),
              ('CredentialBlob',c.POINTER(c.c_ubyte)),('Persist',w.DWORD),
              ('AttributeCount',w.DWORD),('Attributes',c.c_void_p),('TargetAlias',w.LPWSTR),('UserName',w.LPWSTR)]


class KeyStore:
    def __init__(self,namespace='MV_LyricSync/credentials/v1/'):
        self.namespace=namespace
    def target(self,scope):return self.namespace+hashlib.sha256(scope.encode()).hexdigest()
    def api(self):
        if os.name!='nt':raise RuntimeError('密钥保存需要 Windows 凭据管理器。')
        api=c.WinDLL('advapi32',use_last_error=True)
        api.CredWriteW.argtypes=[c.POINTER(Credential),w.DWORD];api.CredWriteW.restype=w.BOOL
        api.CredReadW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.POINTER(c.POINTER(Credential))];api.CredReadW.restype=w.BOOL
        api.CredDeleteW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD];api.CredDeleteW.restype=w.BOOL
        api.CredFree.argtypes=[c.c_void_p];api.CredFree.restype=None
        return api
    def get(self,scope):
        api=self.api();pointer=c.POINTER(Credential)()
        if not api.CredReadW(self.target(scope),1,0,c.byref(pointer)):
            code=c.get_last_error()
            if code==1168:return ''
            raise RuntimeError(f'读取 Windows 密钥失败（错误码 {code}）')
        try:return c.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode('utf-8')
        finally:api.CredFree(pointer)
    def save(self,scope,key):
        if not key.strip():return False
        data=key.strip().encode('utf-8')
        if len(data)>2560:raise ValueError('密钥过长')
        buffer=(c.c_ubyte*len(data)).from_buffer_copy(data)
        value=Credential();value.Type=1;value.TargetName=self.target(scope)
        value.CredentialBlobSize=len(data);value.CredentialBlob=buffer;value.Persist=2;value.UserName='Lyria'
        if not self.api().CredWriteW(c.byref(value),0):raise RuntimeError(f'保存 Windows 密钥失败（错误码 {c.get_last_error()}）')
        return True
    def delete(self,scope):
        if not self.api().CredDeleteW(self.target(scope),1,0) and c.get_last_error()!=1168:
            raise RuntimeError(f'删除 Windows 密钥失败（错误码 {c.get_last_error()}）')


TEXT_SCOPE='https://api.deepseek.com/chat/completions'
