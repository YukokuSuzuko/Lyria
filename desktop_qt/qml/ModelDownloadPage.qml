import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Panel {
    id: page
    objectName: "modelDownloadPage"
    signal closeRequested()
    required property var controller
    required property var state
    readonly property var mc: state.modelCenter || ({})
    readonly property var task: mc.task || ({})
    readonly property bool busy: !!task.running
    readonly property bool paused: !!task.paused
    readonly property string sourceMode: mc.downloadSource || "mirror"
    function cmd(name, payload) { return controller.command(name, payload || {}) }

    focus: visible
    Keys.onEscapePressed: event => {
        if (event.isAutoRepeat) { event.accepted = true; return }
        event.accepted = true
        page.closeRequested()
    }

    ScrollView {
        id: scroll
        anchors.fill: parent; anchors.margins: 24
        contentWidth: availableWidth; clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: scroll.availableWidth; spacing: 14
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 3
                    Label { textFormat: Text.PlainText; text: "模型下载与加速中心"; font.pixelSize: 20; font.weight: Font.DemiBold; color: Theme.ink }
                    Label { textFormat: Text.PlainText; text: "统一设置下载线路与模型存放目录。下载完成或外部拷入模型文件后，全软件自动读取并在数秒内免重启就绪。"; font.pixelSize: 12; color: Theme.label; wrapMode: Text.Wrap; Layout.fillWidth: true }
                }
                ActionButton { objectName: "closeModelCenterButton"; text: "返回（Esc）"; onClicked: page.closeRequested() }
            }

            // Source & directory control card
            Rectangle {
                objectName: "modelCenterConfigCard"
                Layout.fillWidth: true; implicitHeight: cfgCol.implicitHeight + 24
                radius: 12; color: Theme.paper; border.color: Theme.line
                ColumnLayout {
                    id: cfgCol
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14; spacing: 10
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Label { textFormat: Text.PlainText; text: "下载线路"; color: Theme.ink; font.pixelSize: 12; font.weight: Font.DemiBold; Layout.preferredWidth: 84 }
                        ActionButton {
                            objectName: "sourceMirrorButton"
                            text: "🚀 国内镜像源（推荐 · HF-Mirror / 魔搭 / GitHub 加速）"
                            compact: true; checked: page.sourceMode === "mirror"; enabled: !page.busy
                            onClicked: page.cmd("models.setSource", {source: "mirror"})
                        }
                        ActionButton {
                            objectName: "sourceOfficialButton"
                            text: "🌐 官方直连源（Hugging Face / GitHub）"
                            compact: true; checked: page.sourceMode === "official"; enabled: !page.busy
                            onClicked: page.cmd("models.setSource", {source: "official"})
                        }
                        Item { Layout.fillWidth: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Label { textFormat: Text.PlainText; text: "模型位置"; color: Theme.ink; font.pixelSize: 12; font.weight: Font.DemiBold; Layout.preferredWidth: 84 }
                        Rectangle {
                            Layout.fillWidth: true; implicitHeight: 32; radius: 7; color: Theme.slab; border.color: Theme.lineSoft
                            Label {
                                objectName: "modelCachePathLabel"
                                anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
                                verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                                text: page.mc.cacheRoot || ""
                                color: Theme.label; font.pixelSize: 11; elide: Text.ElideMiddle
                            }
                        }
                        ActionButton { objectName: "chooseModelCacheButton"; text: "更改位置…"; compact: true; enabled: !page.busy; onClicked: page.cmd("models.chooseDir") }
                        ActionButton { text: "打开文件夹"; compact: true; onClicked: page.cmd("models.openDir") }
                        ActionButton { objectName: "rescanModelsButton"; text: "重新扫描"; compact: true; enabled: !page.busy; onClicked: page.cmd("models.rescan") }
                    }
                    Label {
                        textFormat: Text.PlainText
                        text: "说明：所有文件下载后均严格校验官方固定 SHA-256 哈希，国内镜像与官方直连文件内容 100% 一致；更改位置后，自动对轴、人声分离与听写会自动从此目录读取。"
                        color: Theme.muted; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap
                    }
                }
            }

            // Live task bar
            Rectangle {
                visible: page.busy || page.paused || !!(page.task.statusText || "")
                Layout.fillWidth: true; implicitHeight: taskCol.implicitHeight + 20
                radius: 10; color: "#FAF6FE"; border.color: Theme.line
                ColumnLayout {
                    id: taskCol
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; spacing: 6
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Label { textFormat: Text.PlainText; text: page.task.statusText || "就绪"; color: page.task.error ? Theme.amber : Theme.ink; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        ActionButton { visible: page.busy && page.task.action === "download"; text: "暂停"; compact: true; onClicked: page.cmd("models.pause") }
                        ActionButton { visible: page.paused; text: "继续下载"; emphasized: true; compact: true; onClicked: page.cmd("models.resume") }
                        ActionButton { visible: page.busy; text: "取消"; compact: true; onClicked: page.cmd("models.cancel") }
                    }
                    ThemeProgressBar { visible: page.busy; Layout.fillWidth: true; from: 0; to: 100; value: page.task.percent || 0 }
                }
            }

            // 5 models list
            Label { textFormat: Text.PlainText; text: "本地 AI 模型清单（共 5 项 · 按需下载即可）"; font.pixelSize: 13; font.weight: Font.DemiBold; color: Theme.ink }
            Repeater {
                model: page.mc.models || []
                Rectangle {
                    required property var modelData
                    objectName: "modelCard_" + modelData.id
                    Layout.fillWidth: true; implicitHeight: mRow.implicitHeight + 22
                    radius: 11; color: Theme.paper; border.color: modelData.ready ? "#B9DFC4" : Theme.line
                    RowLayout {
                        id: mRow
                        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 12
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 3
                            RowLayout {
                                spacing: 8
                                Label { textFormat: Text.PlainText; text: modelData.title; font.pixelSize: 13; font.weight: Font.DemiBold; color: Theme.ink }
                                Badge {
                                    text: modelData.stateText
                                    foreground: modelData.ready ? Theme.green : (modelData.present ? Theme.lilacDark : (modelData.wrongSize ? Theme.amber : Theme.muted))
                                    fill: modelData.ready ? Theme.greenBg : (modelData.present ? Theme.lilacSoft : Theme.slab)
                                }
                            }
                            Label { textFormat: Text.PlainText; text: modelData.desc + " · " + modelData.sizeText + " · 许可：" + modelData.license; font.pixelSize: 11; color: Theme.label; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                        ActionButton {
                            objectName: "downloadModel_" + modelData.id
                            text: modelData.ready ? "已就绪" : "下载"
                            emphasized: !modelData.ready
                            compact: true
                            enabled: !page.busy && !modelData.ready
                            onClicked: page.cmd("models.download", {modelId: modelData.id})
                        }
                        ActionButton {
                            text: "导入本地…"
                            compact: true; enabled: !page.busy
                            onClicked: page.cmd("models.import", {modelId: modelData.id})
                        }
                        ActionButton {
                            text: "校验"
                            compact: true; enabled: !page.busy && modelData.present
                            onClicked: page.cmd("models.verify", {modelId: modelData.id})
                        }
                    }
                }
            }

            // GPU status card
            Rectangle {
                objectName: "modelCenterGpuCard"
                Layout.fillWidth: true; implicitHeight: gpuCol.implicitHeight + 24
                radius: 12; color: Theme.paper; border.color: Theme.line
                ColumnLayout {
                    id: gpuCol
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14; spacing: 8
                    RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 3
                            RowLayout {
                                spacing: 8
                                Label { textFormat: Text.PlainText; text: "显卡与硬件加速"; font.pixelSize: 13; font.weight: Font.DemiBold; color: Theme.ink }
                                Badge {
                                    text: (page.mc.gpu || {}).badgeText || "检测中"
                                    foreground: (page.mc.gpu || {}).active !== "cpu" ? Theme.green : Theme.amber
                                    fill: (page.mc.gpu || {}).active !== "cpu" ? Theme.greenBg : Theme.amberBg
                                }
                            }
                            Label { textFormat: Text.PlainText; text: (page.mc.gpu || {}).deviceText || ""; font.pixelSize: 11; color: Theme.ink; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            Label { textFormat: Text.PlainText; text: (page.mc.gpu || {}).hintText || ""; font.pixelSize: 11; color: Theme.label; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                        ActionButton {
                            visible: !!((page.mc.gpu || {}).canInstallNvidia)
                            text: "安装 NVIDIA 全加速"
                            emphasized: true; compact: true; enabled: !page.busy
                            onClicked: page.cmd("models.installGpu", {vendor: "nvidia"})
                        }
                        ActionButton {
                            visible: !!((page.mc.gpu || {}).canInstallAmd)
                            text: "安装 AMD ROCm 全加速"
                            emphasized: true; compact: true; enabled: !page.busy
                            onClicked: page.cmd("models.installGpu", {vendor: "amd"})
                        }
                        ActionButton { text: "更多配置…"; compact: true; onClicked: page.cmd("settings.acceleration") }
                    }
                }
            }
        }
    }
}
