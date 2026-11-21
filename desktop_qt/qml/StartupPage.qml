import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Layout follows the owner's archived lyria-launch-screen.html: welcome,
// three equal entry cards, and real recent projects below. Theme stays Lilac.
Item {
    id: page
    objectName: "startupPage"
    required property var controller
    required property var state
    property bool active: false
    readonly property var task: state.task || ({})
    readonly property bool busy: !!task.running
    signal qcRequested()
    function cmd(name, payload) { return controller.command(name, payload || {}) }
    ScrollView {
        id: scroll
        anchors.fill: parent
        clip: true; contentWidth: availableWidth
        contentHeight: Math.max(page.height, content.implicitHeight + 64)
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        Item {
            width: scroll.availableWidth
            height: scroll.contentHeight
            ColumnLayout {
                id: content
                width: Math.min(1160, parent.width - 48)
                anchors.horizontalCenter: parent.horizontalCenter
                y: Math.max(28, (page.height - implicitHeight) / 2)
                spacing: 22
                BuildReveal {
                    objectName: "startupHeroReveal"; active: page.active; delay: 60
                    Layout.fillWidth: true; implicitHeight: hero.implicitHeight
                    ColumnLayout {
                        id: hero; width: parent.width; spacing: 6
                        Label { text: "MOONLIT LILAC · 歌词与画面，恰好相遇"; font.pixelSize: 11; font.letterSpacing: .7; color: Theme.lilac }
                        Label { text: "开始制作字幕"; color: Theme.ink; font.pixelSize: 26; font.weight: Font.Bold }
                        Label { text: "根据手头现有的素材选择入口，随时可在同一工作台预览、打轴与核对。"; color: Theme.label; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
                ActionButton { objectName: "startupResume"; visible: !!page.state.canResumeProject; text: "返回当前工程"; emphasized: true; Layout.fillWidth: true; onClicked: page.cmd("project.resume"); tip: "返回离开前的页面，保留现有歌词、播放头和任务。" }
                GridLayout {
                    Layout.fillWidth: true; columns: 3; columnSpacing: 16; rowSpacing: 16
                    Repeater {
                        model: [
                            {mode: "audio", number: "01", glyph: "volume", tag: "音频自动打轴", title: "原词自动打轴", description: "已有演唱原文，通过本地对齐模型生成时间轴，可选人声分离；无原文也可语音转写初稿。", material: "视频 / 音频 + 演唱原文", route: "导入素材 → 选择原词 → 自动打轴", action: "开始音频打轴"},
                            {mode: "ocr", number: "02", glyph: "crop", tag: "画面字幕识别", title: "画面字幕 OCR", description: "框选视频中的硬字幕区域，本地自动识别字幕文字与起止帧时间轴。", material: "含硬字幕视频 · 译文 / 原文可选", route: "导入视频 → 框选字幕 → 本地识别", action: "开始画面 OCR"},
                            {mode: "edit", number: "03", glyph: "timeline", tag: "手动精修打轴", title: "手动打轴编辑", description: "导入现有字幕精细修轴，或从空白开始手动打轴，支持视频、纯音频或无素材编辑。", material: "现有字幕 / 歌词，或空白工程", route: "导入素材（可选） → 歌词编辑与时间轴", action: "直接编辑"}
                        ]
                        BuildReveal {
                            id: entryReveal
                            required property var modelData
                            required property int index
                            objectName: "startupEntryReveal_" + modelData.mode
                            active: page.active; delay: 180 + index * 130
                            Layout.fillWidth: true; Layout.preferredWidth: 360
                            implicitHeight: 340
                            Rectangle {
                                anchors.fill: parent; radius: 16
                                color: entryButton.hovered ? "#FDFCFF" : Theme.paper
                                border.color: entryButton.hovered || entryButton.activeFocus ? Theme.emphasisBorder : Theme.line
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 20; spacing: 12
                                    RowLayout {
                                        Rectangle { width: 38; height: 38; radius: 10; color: Theme.lilacSoft
                                            Image { anchors.centerIn: parent; width: 22; height: 22; source: "../assets/icons/" + modelData.glyph + ".svg"; sourceSize.width: 24; sourceSize.height: 24 }
                                        }
                                        Item { Layout.fillWidth: true }
                                        Label { text: modelData.number + " · " + modelData.tag; color: Theme.muted; font.pixelSize: 10 }
                                    }
                                    Label { text: modelData.title; color: Theme.ink; font.weight: Font.DemiBold; font.pixelSize: content.width < 820 ? 17 : 20; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                    Label { text: modelData.description; color: Theme.label; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                    Item { Layout.fillHeight: true }
                                    Rectangle {
                                        Layout.fillWidth: true; implicitHeight: materialContent.implicitHeight + 22; radius: 9; color: Theme.slab
                                        ColumnLayout { id: materialContent; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 11; spacing: 6
                                            Label { text: "适用素材"; color: Theme.muted; font.pixelSize: 10 }
                                            Label { text: modelData.material; color: Theme.ink; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                            Label { text: modelData.route; color: Theme.lilacDark; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                        }
                                    }
                                    ActionButton { id: entryButton; objectName: "startupEntry_" + modelData.mode; text: modelData.action; emphasized: true; Layout.fillWidth: true; enabled: !page.busy; onClicked: page.cmd("editor.start", {mode: modelData.mode}) }
                                }
                            }
                        }
                    }
                }
                Label { text: "分离人声的波形与频谱可辅助手动打轴，也可直接使用原声音轨。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.muted; font.pixelSize: 11 }
        ActionButton { objectName: "startupEntry_separate"; text: "仅人声分离"; compact: true; enabled: !page.busy; onClicked: page.cmd("editor.start", {mode: "separate"}) }
                BuildReveal {
                    objectName: "startupRecentReveal"; active: page.active; delay: 600
                    Layout.fillWidth: true; implicitHeight: recentContent.implicitHeight + 30
                    Rectangle { anchors.fill: parent; radius: 14; color: Theme.paper; border.color: Theme.line }
                    ColumnLayout {
                        id: recentContent; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 15; spacing: 8
                        RowLayout {
                            Label { text: "最近工程"; color: Theme.ink; font.pixelSize: 13; font.weight: Font.DemiBold }
                            Item { Layout.fillWidth: true }
                            ActionButton { objectName: "startupOpen"; text: "打开本地工程"; glyph: "upload"; compact: true; enabled: !page.busy; onClicked: page.cmd("project.open") }
                            ActionButton { objectName: "startupQc"; visible: false; text: "成片质检"; compact: true; enabled: !page.busy; onClicked: page.qcRequested() }
                        }
                        Label { visible: !(page.state.recentProjects || []).length; text: "还没有最近工程。保存或打开 .lyria 工程后会显示在这里。"; color: Theme.muted; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Repeater {
                            model: page.state.recentProjects || []
                            RowLayout {
                                required property var modelData
                                required property int index
                                Layout.fillWidth: true; spacing: 8
                                ActionButton { text: modelData.name; objectName: "recentProject_" + index; quiet: true; compact: true; enabled: !page.busy; Layout.preferredWidth: 180; tip: modelData.path; onClicked: page.cmd("project.open", {path: modelData.path}) }
                                Label { text: modelData.available ? modelData.path : "路径已失效 · " + modelData.path; color: modelData.available ? Theme.muted : Theme.amber; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideMiddle }
                                ActionButton { text: "移出列表"; quiet: true; compact: true; enabled: !page.busy; tip: "从最近记录中移除（不删除本地文件）"; onClicked: page.cmd("project.recentForget", {path: modelData.path}) }
                            }
                        }
                        ShimmerText { objectName: "startupLoadingText"; visible: page.busy; running: page.busy; text: page.task.label || "正在打开工程…"; Layout.fillWidth: true }
                    }
                }
            }
        }
    }
}
