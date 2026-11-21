import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: advanced
    required property var settingsPanel
    objectName: "advancedSpectrumPopup"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(392, parent ? parent.width - 16 : 392)
    height: Math.min(implicitHeight, parent ? parent.height - 16 : 480)
    padding: 16
    modal: true
    focus: true
    background: Rectangle { radius: 14; color: Theme.paper; border.color: Theme.line }

    component SettingRow: ColumnLayout {
        id: field
        required property string label
        required property string settingKey
        required property var choices
        required property var activeValue
        Layout.fillWidth: true
        spacing: 3
        Label { text: field.label; color: Theme.label }
        Flow {
            Layout.fillWidth: true
            spacing: 4
            Repeater {
                model: field.choices
                ActionButton {
                    required property var modelData
                    objectName: "advanced_" + field.settingKey + "_" + modelData.value
                    text: modelData.title
                    tip: modelData.tip || modelData.title
                    compact: true
                    emphasized: modelData.value === field.activeValue
                    Accessible.checkable: true
                    Accessible.checked: emphasized
                    onClicked: {
                        const change = {}
                        change[field.settingKey] = modelData.value
                        Qt.callLater(advanced.settingsPanel.displaySettings, change)
                    }
                }
            }
        }
    }

    contentItem: ScrollView {
        id: scroll
        contentWidth: availableWidth
        implicitHeight: options.implicitHeight
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            id: options
            width: scroll.availableWidth
            spacing: 8
            Label { text: "高级画面调节"; color: Theme.ink; font.bold: true; font.pixelSize: 15 }
            SettingRow {
                label: "清晰度"; settingKey: "spectrumWindow"; activeValue: advanced.settingsPanel.windowSamples
                choices: [{title:"256",value:256,tip:"短音起点"},{title:"384",value:384,tip:"歌词起音"},
                          {title:"512",value:512,tip:"均衡"},{title:"768",value:768,tip:"持续音"},
                          {title:"1024",value:1024,tip:"长音细节"}]
            }
            SettingRow {
                label: "压暗背景底噪"; settingKey: "spectrumBlack"; activeValue: advanced.settingsPanel.blackLevel
                choices: [{title:"关闭",value:0},{title:"轻",value:.04},{title:"中",value:.08},{title:"强",value:.12}]
            }
            SettingRow {
                label: "显示频率上限"; settingKey: "spectrumFrequency"; activeValue: advanced.settingsPanel.frequencyMaximum
                choices: [{title:"5.5 kHz",value:5500},{title:"8 kHz",value:8000},{title:"10 kHz",value:10000},{title:"11.025 kHz",value:11025}]
            }
            SettingRow {
                label: "纵向频率比例"; settingKey: "spectrumAxis"; activeValue: advanced.settingsPanel.frequencyAxis
                choices: [{title:"均匀分布",value:"linear"},{title:"均衡",value:"balanced"},
                          {title:"展开人声范围",value:"speech"},{title:"传统分段",value:"legacy"}]
            }
            Check { text: "突出人声频段"; checked: advanced.settingsPanel.tiltDb > 0; onClicked: advanced.settingsPanel.displaySettings({spectrumTilt:checked ? 3 : 0}) }
            ActionButton { objectName: "advancedPhysicalSpectrum"; text: "查看未经均衡的原始频谱"; compact: true; onClicked: advanced.settingsPanel.displaySettings({spectrumPreset:"physical",spectrumTilt:0}) }
            RowLayout {
                Layout.fillWidth: true
                ActionButton { objectName: "advancedSpectrumReset"; text: "恢复默认显示"; compact: true; onClicked: advanced.settingsPanel.resetDisplay() }
                Item { Layout.fillWidth: true }
                ActionButton { text: "完成"; primary: true; compact: true; onClicked: advanced.close() }
            }
        }
    }
}
