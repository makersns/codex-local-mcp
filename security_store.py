"""Local macOS Keychain storage; never emits the credential.
"""
import ctypes

KEYCHAIN_SERVICE = "com.sugar.local-mcp.desktop.runtime-key"
KEYCHAIN_ACCOUNT = "desktop"

class CredentialError(Exception):
    pass


class Keychain:
    """Use Security.framework directly; secret data never becomes CLI arguments."""

    def __init__(self, *, interactive: bool = True, service: str = KEYCHAIN_SERVICE,
                 account: str = KEYCHAIN_ACCOUNT):
        self.service = service.encode("utf-8")
        self.account = account.encode("utf-8")
        self.security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        vp, u32 = ctypes.c_void_p, ctypes.c_uint32
        self.security.SecKeychainFindGenericPassword.argtypes = [vp, u32, ctypes.c_char_p, u32, ctypes.c_char_p, ctypes.POINTER(u32), ctypes.POINTER(vp), ctypes.POINTER(vp)]
        self.security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainAddGenericPassword.argtypes = [vp, u32, ctypes.c_char_p, u32, ctypes.c_char_p, u32, vp, ctypes.POINTER(vp)]
        self.security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [vp, vp, u32, vp]
        self.security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self.security.SecKeychainItemFreeContent.argtypes = [vp, vp]
        self.security.SecKeychainItemDelete.argtypes = [vp]
        self.security.SecKeychainItemDelete.restype = ctypes.c_int32
        self.security.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
        self.core.CFRelease.argtypes = [vp]
        self.security.SecKeychainSetUserInteractionAllowed(bool(interactive))

    def _find(self):
        length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        result = self.security.SecKeychainFindGenericPassword(
            None, len(self.service), self.service, len(self.account), self.account,
            ctypes.byref(length), ctypes.byref(data), ctypes.byref(item))
        if result == -25300:
            return None, None
        if result != 0:
            raise CredentialError(f"无法读取登录钥匙串（系统状态 {result}）。请解锁钥匙串后重试。")
        try:
            value = ctypes.string_at(data, length.value).decode("utf-8")
        finally:
            self.security.SecKeychainItemFreeContent(None, data)
        return value, item

    def get(self):
        value, item = self._find()
        if item:
            self.core.CFRelease(item)
        return value

    def set(self, value: str):
        data = value.encode("utf-8")
        buffer = ctypes.create_string_buffer(data)
        previous, item = self._find()
        del previous
        if item:
            try:
                result = self.security.SecKeychainItemModifyAttributesAndData(item, None, len(data), buffer)
            finally:
                self.core.CFRelease(item)
        else:
            item = ctypes.c_void_p()
            result = self.security.SecKeychainAddGenericPassword(
                None, len(self.service), self.service, len(self.account), self.account,
                len(data), buffer, ctypes.byref(item))
            if item:
                self.core.CFRelease(item)
        if result != 0:
            raise CredentialError(f"无法保存登录钥匙串（系统状态 {result}）。")

    def delete(self):
        """Used only for our temporary verification entry, never other credentials."""
        value, item = self._find()
        del value
        if item:
            try:
                result = self.security.SecKeychainItemDelete(item)
                if result != 0:
                    raise CredentialError(f"无法移除测试钥匙串条目（系统状态 {result}）。")
            finally:
                self.core.CFRelease(item)

