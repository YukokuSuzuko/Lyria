"""Session-local explicit consent for exactly one unknown alignment request."""
from copy import deepcopy
import hashlib
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from lyricsync import ai_config


def context(bridge):
    return (bridge.document.id,bridge.epoch,bridge.document.revision,
            bridge._api_scope,hashlib.sha256(bridge._api_key.encode()).hexdigest(),deepcopy(bridge.api_settings))


def available(bridge):
    pending=getattr(bridge,'_alignment_retry',None)
    return bool(pending and pending['context']==context(bridge))


def remember(bridge,offer,payload,maximum):
    bridge._alignment_retry=({'offer':deepcopy(offer),'payload':deepcopy(payload),
                             'maximum':maximum,'context':context(bridge)} if offer else None)
    bridge._refresh(False)


def confirm(bridge,pending):
    offer=pending['offer']
    box=QMessageBox();box.setObjectName('unknownResultRetryDialog')
    box.setTextFormat(Qt.TextFormat.PlainText);box.setWindowTitle('结果未知：确认是否重新发送')
    positions={row.id:str(n) for n,row in enumerate(bridge.document.project.mappings,1)}
    numbers='、'.join(positions.get(identity,'待核实') for identity in offer['targets'])
    project_name=Path(bridge.path).stem if bridge.path else '未命名工程'
    box.setText(f"当前工程：{project_name}\n仅重新发送第 {offer['window']+1} 批（第 {numbers} 句）。")
    box.setInformativeText('服务：'+ai_config.base_url(bridge.api_settings)+'\n模型：'+str(bridge.api_settings.get('api_model',''))+
        '\n上一次请求可能已处理并计费，重新发送可能再次收费。请先核对服务商记录。'+
        '\n仅本批新增一次尝试；已完成批次、旧调用与费用记录保留，预算上限不变；不会自动应用到主时间轴。取消或关闭不会发送。')
    retry=box.addButton('接受重复计费风险，仅重发本批一次',QMessageBox.ButtonRole.AcceptRole)
    later=box.addButton('取消，先核对服务记录',QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(later);box.setEscapeButton(later)
    from .native_theme import apply_dialog_theme
    apply_dialog_theme(box);box.exec()
    return box.clickedButton() is retry


def request(bridge):
    if bridge._task.get('running') or bridge._candidate_job:
        raise ValueError('请等待当前任务结束后再确认重发。')
    if not available(bridge):
        bridge._alignment_retry=None
        raise ValueError('工程、修订或服务配置已改变；请重新查看当前范围的结果。')
    pending=bridge._alignment_retry
    bridge._alignment_retry=None  # Reject reentrant/repeated clicks during the dialog.
    bridge._refresh(False)
    if not confirm(bridge,pending):
        if pending['context']==context(bridge):bridge._alignment_retry=pending
        bridge._refresh(False);return
    if pending['context']!=context(bridge):
        raise ValueError('确认期间工程或服务配置已改变；未发送请求。')
    bridge._api_alignment(pending['payload'],False,pending['maximum'],force_retry=pending['offer'])
