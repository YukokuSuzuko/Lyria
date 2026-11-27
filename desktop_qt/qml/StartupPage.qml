import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Layout keeps three equal entry cards + human-review banner + recent projects. Theme stays Moonlit Lilac.
Item {
    id: page
    objectName: "startupPage"
    required property var controller
    required property var state
    property bool active: false
    readonly property var task: state.task || ({})
    readonly property bool busy: !!task.running
    signal qcRequested()
    signal modelsRequested()
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
                y: Math.max(24, (page.height - implicitHeight) / 2)
                spacing: 18
                BuildReveal {
                    objectName: "startupHeroReveal"; active: page.active; delay: 60
                    Layout.fillWidth: true; implicitHeight: hero.implicitHeight
                    ColumnLayout {
                        id: hero; width: parent.width; spacing: 6
                        Label { text: "MOONLIT LILAC · 歌词与画面，恰好相遇"; font.pixelSize: 11; font.letterSpacing: .7; color: Theme.lilac }
                        Label { text: "开始制作字幕"; color: Theme.ink; font.pixelSize: 26; font.weight: Font.Bold }
                        Label { text: "根据手头素材选择入口，可在同一工作台随时预览画面、核对文本并精细修轴。"; color: Theme.label; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
                ActionButton { objectName: "startupResume"; visible: !!page.state.canResumeProject; text: "返回当前工程"; emphasized: true; Layout.fillWidth: true; onClicked: page.cmd("project.resume"); tip: "返回离开前的页面，保留现有歌词、播放头和任务。" }
                GridLayout {
                    Layout.fillWidth: true; columns: 3; columnSpacing: 16; rowSpacing: 16
                    Repeater {
                        model: [
                            {mode: "audio", number: "01", glyph: "volume", tag: "听声对词", title: "AI 自动对轴", description: "让歌词自动贴合人声演唱节奏，支持伴奏分离与无词听写初稿。", caveat: "⚠ 适合单人清晰演唱；多人合唱、副歌厚重和声或词句重叠处易出偏差，需试听微调。", caveatWarn: true, material: "音视频 + 歌词文本（无词可听写）", route: "导入歌曲 → 载入歌词 → 自动生成", action: "开始自动对轴"},
                            {mode: "ocr", number: "02", glyph: "crop", tag: "识画取轴", title: "画面字幕 OCR", description: "框选视频中的硬字幕位置，自动提取词句并生成帧级时间轴。", caveat: "⚠ 仅针对整句直接切入切出的无特效字幕优化；渐入渐出、逐字浮现或花字易漏识偏移。", caveatWarn: true, material: "带硬字幕的视频（译文 / 原文选填）", route: "导入视频 → 框选字幕 → 自动提取", action: "开始字幕识别"},
                            {mode: "edit", number: "03", glyph: "timeline", tag: "逐帧创作", title: "手动模式", description: "导入已有字幕微调出入点，或伴随波形与频谱从零手打时间轴。", caveat: "• 支持逐帧步进、80% 人声频谱对照、剃刀切分、多选合并与导出双语 SRT / ASS。", caveatWarn: false, material: "现有字幕 / 歌词，或空白工程", route: "载入素材 → 对照音轨 → 精细修轴", action: "直接进入编辑"}
                        ]
                        BuildReveal {
                            id: entryReveal
                            required property var modelData
                            required property int index
                            objectName: "startupEntryReveal_" + modelData.mode
                            active: page.active; delay: 180 + index * 130
                            Layout.fillWidth: true; Layout.preferredWidth: 360
                            implicitHeight: 376
                            Rectangle {
                                anchors.fill: parent; radius: 16
                                color: entryButton.hovered ? "#FDFCFF" : Theme.paper
                                border.color: entryButton.hovered || entryButton.activeFocus ? Theme.emphasisBorder : Theme.line
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 18; spacing: 10
                                    RowLayout {
                                        Rectangle { width: 38; height: 38; radius: 10; color: Theme.lilacSoft
                                            Image { anchors.centerIn: parent; width: 22; height: 22; source: "../assets/icons/" + modelData.glyph + ".svg"; sourceSize.width: 24; sourceSize.height: 24 }
                                        }
                                        Item { Layout.fillWidth: true }
                                        Label { text: modelData.number + " · " + modelData.tag; color: Theme.muted; font.pixelSize: 10 }
                                    }
                                    Label { text: modelData.title; color: Theme.ink; font.weight: Font.DemiBold; font.pixelSize: content.width < 820 ? 17 : 20; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                    Label { text: modelData.description; color: Theme.label; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        implicitHeight: caveatLabel.implicitHeight + 14
                                        radius: 8
                                        color: modelData.caveatWarn ? "#FFF8ED" : Theme.lilacSoft
                                        border.color: modelData.caveatWarn ? "#F0D8B5" : Theme.lineSoft
                                        Label {
                                            id: caveatLabel
                                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                            anchors.leftMargin: 10; anchors.rightMargin: 10
                                            text: modelData.caveat
                                            color: modelData.caveatWarn ? "#8A5416" : Theme.lilacDark
                                            font.pixelSize: 11
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                    Item { Layout.fillHeight: true }
                                    Rectangle {
                                        Layout.fillWidth: true; implicitHeight: materialContent.implicitHeight + 20; radius: 9; color: Theme.slab
                                        ColumnLayout { id: materialContent; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10; spacing: 5
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
                Rectangle {
                    id: reviewBanner
                    objectName: "startupHumanReviewBanner"
                    Layout.fillWidth: true
                    implicitHeight: bannerRow.implicitHeight + 24
                    radius: 14
                    color: "#FAF6FE"
                    border.color: "#DFCFF0"
                    RowLayout {
                        id: bannerRow
                        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 16; anchors.rightMargin: 16
                        spacing: 14
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 4
                            Label {
                                textFormat: Text.PlainText
                                text: "🛡  AI 与自动识别仅作效率辅助，最终成片请务必由人工把关"
                                color: Theme.ink; font.pixelSize: 13; font.weight: Font.DemiBold
                                Layout.fillWidth: true; wrapMode: Text.Wrap
                            }
                            Label {
                                textFormat: Text.PlainText
                                text: "多人合唱、副歌和声与词句重叠容易导致自动对轴漂移；画面 OCR 仅对整句直切无特效硬字幕效果最佳（渐入渐出亦属特效）。导出前请对照原声与画面复核。"
                                color: Theme.label; font.pixelSize: 11
                                Layout.fillWidth: true; wrapMode: Text.Wrap
                            }
                        }
                        ActionButton {
                            objectName: "startupOnboardingButton"
                            text: "新手操作指引"
                            compact: true
                            tip: "查看 5 步交互式快速上手指南"
                            onClicked: page.cmd("onboarding.open")
                        }
                        ActionButton {
                            objectName: "startupModelsButton"
                            text: "模型下载与加速"
                            emphasized: true
                            compact: true
                            enabled: !page.busy
                            tip: "选择国内镜像源或官方源下载模型、配置全局目录与显卡加速"
                            onClicked: page.modelsRequested()
                        }
                        ActionButton {
                            objectName: "startupEntry_separate"
                            text: "单独分离人声"
                            compact: true
                            enabled: !page.busy
                            onClicked: page.cmd("editor.start", {mode: "separate"})
                        }
                    }
                }
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
