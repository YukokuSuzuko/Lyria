import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Panel {
    id: page
    objectName: "settingsPage"
    signal closeRequested()
    focus: visible
    Keys.onEscapePressed: event => {
        if (event.isAutoRepeat) { event.accepted = true; return }
        event.accepted = true
        page.closeRequested()
    }
    required property var controller
    required property var state
    readonly property var settings: state.settings || ({})
    readonly property var capabilities: state.capabilities || ({})
    function cmd(name, payload) { controller.command(name, payload || {}) }
    ScrollView {
        anchors.fill: parent; anchors.margins: 24; contentWidth: availableWidth; clip: true
        ColumnLayout {
            width: parent.width; spacing: 18
            RowLayout {
                Layout.fillWidth: true
                Label { textFormat: Text.PlainText; text: "设置"; font.pixelSize: 20; font.weight: Font.DemiBold; color: Theme.ink; Layout.fillWidth: true }
                ActionButton { objectName: "closeSettingsButton"; text: "返回（Esc）"; onClicked: page.closeRequested() }
            }
            Label { textFormat: Text.PlainText; text: "界面与播放"; font.pixelSize: 13; font.weight: Font.DemiBold; color: Theme.ink }
            Check { text: "界面动效"; checked: page.settings.motion !== false; onToggled: page.cmd("settings.set", {key: "motion", value: checked}) }
            Check { text: "默认显示字幕预览"; checked: page.settings.subtitlePreview !== false; onToggled: page.cmd("settings.set", {key: "subtitlePreview", value: checked}) }
            Check { text: "播放器玻璃效果"; checked: page.settings.playerGlass !== false; onToggled: page.cmd("settings.set", {key: "playerGlass", value: checked}) }
            Check { text: "视频画面圆角"; checked: page.settings.playerRounded !== false; onToggled: page.cmd("settings.set", {key: "playerRounded", value: checked}) }
            Label { textFormat: Text.PlainText; text: "关闭后将取消面板展开与页面切换过渡动画，不影响视频播放与打轴响应。"; color: Theme.muted; font.pixelSize: 11 }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.lineSoft }
            Label { textFormat: Text.PlainText; text: "工程与自动备份"; font.pixelSize: 13; font.weight: Font.DemiBold; color: Theme.ink }
            Label { textFormat: Text.PlainText; text: "编辑过程中会自动生成本地备份，崩溃或误关后可通过「恢复备份」找回；正式保存工程请按 Ctrl+S。"; color: Theme.label; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Flow { Layout.fillWidth: true; spacing: 8
                ActionButton { text: "打开工程"; onClicked: page.cmd("project.open") }
                ActionButton { text: "恢复备份"; enabled: page.capabilities["project.recover"] === true; onClicked: page.cmd("project.recover") }
                ActionButton { text: "KTV 字幕转换"; enabled: page.capabilities["ktv.convert"] === true; onClicked: page.cmd("ktv.convert") }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.lineSoft }
            Label { textFormat: Text.PlainText; text: "运行日志与诊断"; font.pixelSize: 13; font.weight: Font.DemiBold; color: Theme.ink }
            Label { textFormat: Text.PlainText; text: "诊断日志仅记录程序报错信息与版本号，不包含歌词内容、文件路径或 API 密钥，仅供反馈问题时手动导出。"; color: Theme.label; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Label { textFormat: Text.PlainText; text: (page.state.diagnostics || {}).persistent === false ? "日志无法写入磁盘，本次仍可手动导出最近记录。" : (page.state.diagnostics || {}).temporary === true ? "默认日志目录无法写入，已改存至系统临时目录。" : "运行日志保存在本地，便于排查问题。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Flow { Layout.fillWidth: true; spacing: 8
                ActionButton { objectName: "exportDiagnosticsButton"; text: "导出诊断日志"; glyph: "export"; enabled: page.capabilities["diagnostics.export"] === true; onClicked: page.cmd("diagnostics.export") }
            }


        }
    }
}
