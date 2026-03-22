# coding=utf-8
"""
Minimal ctypes structure definitions for HCNetSDK + PlayCtrl.
Only structures required for login / real-time preview / decode are included.
"""

import sys
from ctypes import *

# ---------------------------------------------------------------------------
# Calling convention (stdcall on Windows, cdecl on Linux)
# ---------------------------------------------------------------------------
if sys.platform == 'win32':
    _fun_ctype = WINFUNCTYPE
else:
    _fun_ctype = CFUNCTYPE

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NET_DVR_SYSHEAD = 1
NET_DVR_STREAMDATA = 2
NET_DVR_AUDIOSTREAMDATA = 3
NET_DVR_PRIVATE_DATA = 112


# ---------------------------------------------------------------------------
# HCNetSDK structures
# ---------------------------------------------------------------------------

class NET_DVR_DEVICEINFO_V30(Structure):
    _fields_ = [
        ("sSerialNumber", c_byte * 48),
        ("byAlarmInPortNum", c_byte),
        ("byAlarmOutPortNum", c_byte),
        ("byDiskNum", c_byte),
        ("byDVRType", c_byte),
        ("byChanNum", c_byte),
        ("byStartChan", c_byte),
        ("byAudioChanNum", c_byte),
        ("byIPChanNum", c_byte),
        ("byZeroChanNum", c_byte),
        ("byMainProto", c_byte),
        ("bySubProto", c_byte),
        ("bySupport", c_byte),
        ("bySupport1", c_byte),
        ("bySupport2", c_byte),
        ("wDevType", c_uint16),
        ("bySupport3", c_byte),
        ("byMultiStreamProto", c_byte),
        ("byStartDChan", c_byte),
        ("byStartDTalkChan", c_byte),
        ("byHighDChanNum", c_byte),
        ("bySupport4", c_byte),
        ("byLanguageType", c_byte),
        ("byVoiceInChanNum", c_byte),
        ("byStartVoiceInChanNo", c_byte),
        ("bySupport5", c_byte),
        ("bySupport6", c_byte),
        ("byMirrorChanNum", c_byte),
        ("wStartMirrorChanNo", c_uint16),
        ("bySupport7", c_byte),
        ("byRes2", c_byte),
    ]


class NET_DVR_LOCAL_SDK_PATH(Structure):
    _fields_ = [
        ('sPath', c_char * 256),
        ('byRes', c_byte * 128),
    ]


class NET_DVR_PREVIEWINFO(Structure):
    _fields_ = [
        ('lChannel', c_uint32),
        ('dwStreamType', c_uint32),
        ('dwLinkMode', c_uint32),
        ('hPlayWnd', c_uint32),
        ('bBlocked', c_uint32),
        ('bPassbackRecord', c_uint32),
        ('byPreviewMode', c_ubyte),
        ('byStreamID', c_ubyte * 32),
        ('byProtoType', c_ubyte),
        ('byRes1', c_ubyte),
        ('byVideoCodingType', c_ubyte),
        ('dwDisplayBufNum', c_uint32),
        ('byNPQMode', c_ubyte),
        ('byRecvMetaData', c_ubyte),
        ('byDataType', c_ubyte),
        ('byRes', c_ubyte * 213),
    ]


class NET_DVR_LOCAL_GENERAL_CFG(Structure):
    _fields_ = [
        ("byExceptionCbDirectly", c_ubyte),
        ("byNotSplitRecordFile", c_ubyte),
        ("byResumeUpgradeEnable", c_ubyte),
        ("byAlarmJsonPictureSeparate", c_ubyte),
        ("byRes", c_ubyte * 4),
        ("i64FileSize", c_uint64),
        ("dwResumeUpgradeTimeout", c_uint32),
        ("byAlarmReconnectMode", c_ubyte),
        ("byStdXmlBufferSize", c_ubyte),
        ("byMultiplexing", c_ubyte),
        ("byFastUpgrade", c_ubyte),
        ("byRes1", c_ubyte * 232),
    ]


# ---------------------------------------------------------------------------
# PlayCtrl structures
# ---------------------------------------------------------------------------

class FRAME_INFO(Structure):
    _fields_ = [
        ('nWidth', c_uint32),
        ('nHeight', c_uint32),
        ('nStamp', c_uint32),
        ('nType', c_uint32),
        ('nFrameRate', c_uint32),
        ('dwFrameNum', c_uint32),
    ]


# ---------------------------------------------------------------------------
# Callback types
# ---------------------------------------------------------------------------

# NET_DVR_RealPlay_V40 real-time data callback
REALDATACALLBACK = _fun_ctype(
    None,       # return void
    c_long,     # lPlayHandle
    c_ulong,    # dwDataType
    POINTER(c_ubyte),  # pBuffer
    c_ulong,    # dwBufSize
    c_void_p,   # pUser
)

# PlayM4 decode callback (software decode)
DECCBFUN = _fun_ctype(
    None,       # return void
    c_long,     # nPort
    POINTER(c_char),   # pBuf
    c_long,     # nSize
    POINTER(FRAME_INFO),  # pFrameInfo
    c_void_p,   # nUser
    c_void_p,   # nReserved2
)
