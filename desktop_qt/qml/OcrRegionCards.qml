import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: cards
    radius: 8; color: Theme.slab; border.color: Theme.lineSoft
    implicitHeight: contents.implicitHeight + 18
    required property var controller
    required property var state
    property bool regionsVisible: false
    signal visibilityRequested(bool value)
    signal showRegions()
    signal drawRequested()
    function send(action, regionId, value) {
        var ok = controller.command("roi.multi", {action: action, id: regionId || "", value: value})
        if (ok) showRegions()
    }
    ColumnLayout {
        id: contents; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 9; spacing: 5
    RowLayout {
        Layout.fillWidth: true
        Label { text: "画面识别选区"; Layout.fillWidth: true; color: Theme.ink; font.pixelSize: 12; font.weight: Font.DemiBold }
        Check { objectName: "regionVisibilityCheck"; text: "显示选区"; enabled: (cards.state.regionCards || []).length > 0; checked: cards.regionsVisible; onClicked: cards.visibilityRequested(checked) }
    }
    Flow {
        Layout.fillWidth: true
        spacing: 5
        ActionButton { objectName: "detectOcrRegions"; text: "自动检测区域"; compact: true; onClicked: if (cards.controller.command("roi.auto", {})) cards.showRegions() }
        ActionButton { objectName: "addOcrRegion"; text: "＋ 手动框选"; compact: true; enabled: (cards.state.regionCards || []).length < 4; onClicked: cards.drawRequested() }
        ActionButton { objectName: "clearOcrRegions"; text: "清空"; compact: true; enabled: (cards.state.regionCards || []).length > 0; onClicked: cards.send("clear") }
    }
    Label {
        visible: cards.state.ocrRegionsConfigured === true && !cards.state.ocrMultiRegion
        Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 11; color: Theme.label
        text: "当前使用单框。下方是保留的多选区设置，修改卡片即可启用。"
    }
    Repeater {
        model: cards.state.regionCards || []
        delegate: RowLayout {
            id: row
            required property int index
            required property var modelData
            Layout.fillWidth: true
            spacing: 3
            Check {
                Layout.preferredWidth: 19
                Accessible.name: "启用" + row.modelData.name
                checked: row.modelData.enabled
                font.pixelSize: 11
                padding: 1
                onClicked: cards.send("enabled",row.modelData.id,checked)
            }
            Rectangle { width: 9; height: 9; radius: width / 2; color: Theme.regionColor(row.modelData.paletteIndex || 0) }
            TextField {
                background: Item {}
                text: row.modelData.name
                Layout.preferredWidth: 53; Layout.minimumWidth: 27; implicitHeight: 26
                font.pixelSize: 11; selectByMouse: true; maximumLength: 80
                Accessible.name: "选区名称"
                onEditingFinished: if (text.trim() && text.trim() !== row.modelData.name) cards.send("name", row.modelData.id, text.trim())
            }
            SpeakerField {
                objectName:"roiBinding_"+row.modelData.id+"_speaker_name"; inputName:"roiSpeaker_"+row.modelData.id
                Layout.fillWidth:true; Layout.minimumWidth:55; Layout.preferredWidth:90; implicitHeight:26
                scopeKey:JSON.stringify([(cards.state.project || {}).id,row.modelData.id]); nameValue:row.modelData.speaker || ""
                nameColor: (choices.find(item => item.name === nameValue) || {}).color || ""
                choices: cards.state.reviewSpeakers || []; emptyHint:"说话人：不指定"
                onSubmitted: value => cards.send("speaker_name",row.modelData.id,value)
            }
            ChoiceBox {
                objectName:"roiBinding_"+row.modelData.id+"_style_id"; implicitHeight:26; font.pixelSize:11
                Layout.fillWidth:true; Layout.minimumWidth:40; Layout.preferredWidth:90
                textRole:"name"; displayText:"样式："+currentText; Accessible.name:"归属样式"
                model:[{name:"工程主样式",id:""}].concat(((cards.state.reviewStyles || {}).items || []).filter(v => v.id !== "default"))
                currentIndex:Math.max(0,model.findIndex(item => item.id===row.modelData.style))
                onActivated:index => cards.send("style_id",row.modelData.id,model[index].id)
            }
            ActionButton { text:"编辑"; compact:true; tip:"编辑此选区使用的样式"; onClicked:cards.controller.command("style.editCurrent",{regionId:row.modelData.id}) }
            ToolButton { text: "×"; background: Item {} implicitWidth: 24; Accessible.name: "删除本选区"; onClicked: cards.send("remove", row.modelData.id) }
        }
    }
    Label {
        Layout.fillWidth: true
        visible: !!text
        wrapMode: Text.Wrap
        color: Theme.muted
        font.pixelSize: 11
        text: {
            var all=(cards.state.regionCards || []).filter(function(r){return r.enabled})
            for (var i=0;i<all.length;i++) for (var j=i+1;j<all.length;j++) {
                var a=all[i], b=all[j]
                var area=Math.max(0, Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y))
                if (area>.08*Math.min(a.width*a.height,b.width*b.height)) return "选区有重叠。扫描时会对同一位置的相同文字去重。"
            }
            return ""
        }
    }
    }
}
