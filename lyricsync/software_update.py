"""Fail-closed update availability for unsigned development candidates.

No endpoint, trust key, installer or network operation is supplied by a project
file or a remote manifest. A future updater must replace this gate only after
its release trust and transactional installation contract is established.
"""
from dataclasses import dataclass
from . import __version__


@dataclass(frozen=True)
class UpdateStatus:
    current_version: str
    state: str = 'not_configured'
    can_check: bool = False
    can_download: bool = False
    can_install: bool = False
    title: str = '尚未配置可信更新源'
    detail: str = ('此测试版尚未配置可验证的发布源、发布签名和安全安装流程。'
                   '未检查远端版本，不代表当前已是最新版；不会自动下载或运行更新。')


def startup_status():
    """Local release-policy check only. It must not perform network I/O."""
    return UpdateStatus(current_version=__version__)
