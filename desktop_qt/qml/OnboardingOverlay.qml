import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: overlay
    objectName: "onboardingOverlay"
    required property var controller
    required property var state
    readonly property bool isOpen: state.onboardingOpen === true
    property int step: 0
    readonly property int totalSteps: 5

    visible: isOpen
    enabled: isOpen
    z: 960
    focus: isOpen

    onIsOpenChanged: if (isOpen) step = 0

    Keys.onEscapePressed: {
        controller.command("onboarding.complete", {})
    }
    Keys.onLeftPressed: if (step > 0) step -= 1
    Keys.onRightPressed: if (step < totalSteps - 1) step += 1

    readonly property var stepsData: [
        {
            tag: "STEP 01 · 工作入口与原则",
            title: "三种创作入口，AI 提效、人工把关",
            lead: "无论是人声对词、视频抓字还是纯手打校对，都可以随时在左上角模式胶囊间切换：",
            cards: [
                { badge: "默认推荐 NextFire", badgeColor: "#5E3B9E", name: "01  AI 自动对轴", desc: "用人声贴合歌词节奏，日语卡拉 OK 默选 NextFire；副歌厚重和声或合唱处建议复听微调。" },
                { badge: "无特效直切字幕", badgeColor: "#2C7A5B", name: "02  画面字幕 OCR", desc: "框选视频中的硬字幕位置提取时间轴；渐入渐出等特效字幕建议转手动模式微调。" },
                { badge: "全功能手工精细修轴", badgeColor: "#63419C", name: "03  手动模式", desc: "伴随 80% 亮度频谱与波形逐帧敲定入出点，自由切分合并与导出双语 SRT / ASS。" }
            ],
            keys: [],
            tip: "提示：顶部左上角的模式胶囊与「工具 → 人声分离…」随时待命，不必退回启动页。"
        },
        {
            tag: "STEP 02 · 模型中心与加速",
            title: "国内镜像秒下模型，修改目录全局同步",
            lead: "内置「模型下载中心」已为国内网络环境预设镜像源，开箱自带 DirectML 显卡基础加速：",
            cards: [
                { badge: "默认国内加速", badgeColor: "#5E3B9E", name: "一键换源下载", desc: "内置 HF-Mirror / 魔搭 / GitHub 加速通道，下载卡顿随时可暂停、续传或切换官方直连。" },
                { badge: "全局即时读取", badgeColor: "#2C7A5B", name: "改路径即全盘联动", desc: "把模型放到空间充足的 D / E / F 盘后，自动打轴、人声分离和听写模块立即同步生效。" },
                { badge: "免重启热加载", badgeColor: "#63419C", name: "DirectML + 全功能扩展", desc: "Intel / AMD / N 卡默认 DirectML 直接跑 OCR 与分离；N 卡 / 新 A 卡可在模型中心一键装全速包。" }
            ],
            keys: [],
            tip: "提示：若已有离线下载好的模型文件夹，在模型中心点「导入文件夹」免重新下载。"
        },
        {
            tag: "STEP 03 · 键盘打轴连招",
            title: "左手键盘走帧定位，效率翻倍不累手",
            lead: "熟记这一组常用快捷键，不用频繁用鼠标拖拉也能打出电影级帧精确时间轴：",
            cards: [],
            keys: [
                { key: "Space", label: "播放 / 暂停预览" },
                { key: "← / →", label: "后退 / 前进 1 帧（Shift 为 5 帧）" },
                { key: "[  /  ]", label: "把当前帧设为本句入点 / 出点" },
                { key: "M", label: "在当前帧放置独立参考标记点" },
                { key: "C", label: "切换剃刀工具：点击片段一刀两断" },
                { key: "Ctrl + Enter", label: "确认当前句并自动跳至下一句" }
            ],
            tip: "小技巧：开启底部时间轴「打轴模式」后，边放歌边连按两下 D 键即可直接记录一句的起止时间！"
        },
        {
            tag: "STEP 04 · 频谱对波、多选与配对",
            title: "80% 频谱捕捉字头，Ctrl 多选批量操作",
            lead: "导入音视频后，时间轴默认呈现 80% 亮度人声频谱，配合多选与配对台快速成轴：",
            cards: [
                { badge: "Alt + V 切换原声/人声", badgeColor: "#5E3B9E", name: "人声频谱对齐起音", desc: "高亮竖纹对应辅音和换气位置，拖拽句块边沿贴近亮线，可同步按顶部「分秒 / 帧数」查看刻度。" },
                { badge: "Ctrl / Shift + 左键", badgeColor: "#2C7A5B", name: "强高亮多选成组", desc: "列表与时间轴同时以醒目紫边框高亮选中句；右键即可「合并选中」、批量设说话人或样式。" },
                { badge: "OCR / 译文 / 原文三栏", badgeColor: "#63419C", name: "高效配对工作台", desc: "用「一键按 OCR 配对」或拖拽匹配原词译文，搭配译文剃刀轻松应对 1 句拆多行。" }
            ],
            keys: [],
            tip: "提示：想给多人合唱赋色时，在核对行选中后按 Alt+1~9 即可指派说话人并跳到下一句。"
        },
        {
            tag: "STEP 05 · Aegisub 样式与导出",
            title: "双栏样式库秒开管理，一键导出双语 SRT / ASS",
            lead: "从字幕字体排布到最终双语文件导出，全部保留完整撤销历史与原始素材保护：",
            cards: [
                { badge: "经典对称双栏", badgeColor: "#5E3B9E", name: "字幕样式管理器", desc: "左「样式库」右「当前工程」，支持多套目录分类、排序按钮条、颜色圆点预览及 ASS 脚本导入导出。" },
                { badge: "毫秒级响应", badgeColor: "#2C7A5B", name: "字体瀑布流预览", desc: "实时试看歌词字体效果并自动标注「⚠ 缺字」，支持深色字幕底板与浅色纸面底板切换对比。" },
                { badge: "单语 / 双语 UTF-8", badgeColor: "#63419C", name: "原生 SRT 与 ASS 导出", desc: "菜单与导出页均支持「快速导出 SRT…」及双语上下轨配置，随时可一键撤销回到历史节点。" }
            ],
            keys: [],
            tip: "准备好了！点击下方「开始创作」，之后随时可在顶部「帮助 / 设置 → 新手操作指引…」再次唤醒。"
        }
    ]

    readonly property var activeStep: stepsData[step]

    // Dimmed Game-HUD backdrop
    Rectangle {
        anchors.fill: parent
        color: "#A6191326"
        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            onClicked: {}
        }
    }

    Rectangle {
        id: card
        objectName: "onboardingCard"
        anchors.centerIn: parent
        width: Math.min(720, overlay.width - 48)
        height: Math.min(contentCol.implicitHeight + 44, overlay.height - 48)
        radius: 18
        color: Theme.paper
        border.color: "#D3C2EC"
        border.width: 1.5

        ColumnLayout {
            id: contentCol
            anchors.fill: parent
            anchors.margins: 22
            spacing: 14

            // Top HUD progress bar + Close button
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Rectangle {
                    radius: 8; color: Theme.lilacSoft; border.color: "#DCCDF2"
                    implicitWidth: stepTagLabel.implicitWidth + 16; implicitHeight: 24
                    Label {
                        id: stepTagLabel; anchors.centerIn: parent
                        text: overlay.activeStep.tag; color: Theme.lilacDark
                        font.pixelSize: 11; font.weight: Font.DemiBold
                    }
                }
                Row {
                    spacing: 5
                    Repeater {
                        model: overlay.totalSteps
                        Rectangle {
                            required property int index
                            width: index === overlay.step ? 26 : 10
                            height: 7; radius: 3.5
                            color: index === overlay.step ? "#5E3B9E" : (index < overlay.step ? "#A78DE0" : "#E5DDF2")
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                onClicked: overlay.step = index
                            }
                        }
                    }
                }
                Item { Layout.fillWidth: true }
                ActionButton {
                    objectName: "onboardingSkipButton"
                    text: "跳过指引"
                    quiet: true; compact: true
                    onClicked: overlay.controller.command("onboarding.complete", {})
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5
                Label {
                    objectName: "onboardingTitle"
                    text: overlay.activeStep.title
                    font.pixelSize: 20; font.weight: Font.Bold; color: Theme.ink
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                }
                Label {
                    text: overlay.activeStep.lead
                    font.pixelSize: 13; color: Theme.label
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                }
            }

            // Feature cards (Steps 1, 2, 4, 5)
            ColumnLayout {
                visible: overlay.activeStep.cards.length > 0
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: overlay.activeStep.cards
                    Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: cardInner.implicitHeight + 18
                        radius: 11; color: "#FAF8FD"; border.color: "#E4DCF2"
                        ColumnLayout {
                            id: cardInner
                            anchors.fill: parent; anchors.margins: 10; spacing: 4
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Label {
                                    text: modelData.name; color: Theme.ink
                                    font.pixelSize: 13; font.weight: Font.DemiBold
                                }
                                Rectangle {
                                    radius: 6; color: Qt.tint("#FFFFFF", Qt.rgba(Qt.color(modelData.badgeColor).r, Qt.color(modelData.badgeColor).g, Qt.color(modelData.badgeColor).b, 0.14))
                                    implicitWidth: badgeText.implicitWidth + 12; implicitHeight: 20
                                    Label {
                                        id: badgeText; anchors.centerIn: parent
                                        text: modelData.badge; color: modelData.badgeColor
                                        font.pixelSize: 10; font.weight: Font.DemiBold
                                    }
                                }
                                Item { Layout.fillWidth: true }
                            }
                            Label {
                                text: modelData.desc; color: Theme.label
                                font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap
                            }
                        }
                    }
                }
            }

            // Keycap Grid (Step 3)
            GridLayout {
                visible: overlay.activeStep.keys.length > 0
                Layout.fillWidth: true
                columns: 2; columnSpacing: 10; rowSpacing: 8
                Repeater {
                    model: overlay.activeStep.keys
                    Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: 46; radius: 10
                        color: "#FAF8FD"; border.color: "#E4DCF2"
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; spacing: 10
                            Rectangle {
                                implicitWidth: Math.max(46, capText.implicitWidth + 18)
                                implicitHeight: 28; radius: 7
                                color: "#FFFFFF"; border.color: "#CBB8EA"; border.width: 1.5
                                Label {
                                    id: capText; anchors.centerIn: parent
                                    text: modelData.key; color: "#5E3B9E"
                                    font.pixelSize: 11; font.weight: Font.Bold
                                }
                            }
                            Label {
                                text: modelData.label; color: Theme.ink
                                font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap
                            }
                        }
                    }
                }
            }

            // Tip Bar
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: tipText.implicitHeight + 14
                radius: 9; color: "#F2ECFC"; border.color: "#DFD1F4"
                Label {
                    id: tipText
                    anchors.fill: parent; anchors.margins: 8
                    text: overlay.activeStep.tip
                    color: Theme.lilacDark; font.pixelSize: 12; wrapMode: Text.Wrap
                }
            }

            // Footer controls
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Label {
                    text: "第 " + (overlay.step + 1) + " / " + overlay.totalSteps + " 步"
                    color: Theme.muted; font.pixelSize: 11
                }
                Item { Layout.fillWidth: true }
                ActionButton {
                    objectName: "onboardingPrevButton"
                    text: "上一步"
                    visible: overlay.step > 0
                    onClicked: if (overlay.step > 0) overlay.step -= 1
                }
                ActionButton {
                    objectName: "onboardingNextButton"
                    text: overlay.step < overlay.totalSteps - 1 ? "下一步" : "开始创作"
                    primary: true
                    onClicked: {
                        if (overlay.step < overlay.totalSteps - 1) overlay.step += 1
                        else overlay.controller.command("onboarding.complete", {})
                    }
                }
            }
        }
    }
}
