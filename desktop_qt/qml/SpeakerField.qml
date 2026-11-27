import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: field
    property string nameValue: ""
    property string nameColor: ""
    property string scopeKey: ""
    property string inputName: ""
    property string menuName: ""
    property string emptyHint: "说话人"
    property int mixedCount: 0
    property var choices: []
    signal submitted(string value)
    readonly property bool editing: editor.activeFocus || editor.inputMethodComposing
    property bool filtering: false
    property string editingScope: ""
    readonly property var suggestions: filtering && editor.text.trim()
        ? choices.filter(item => item.name.toLocaleLowerCase().startsWith(editor.text.trim().toLocaleLowerCase())) : choices
    implicitWidth: 150; implicitHeight: 28
    radius: 7; color: Theme.paper; border.color: editor.activeFocus ? Theme.lilac : Theme.line

    function finish() {
        suggestionsPopup.close()
        filtering = false
        if (editor.activeFocus) field.forceActiveFocus()
    }
    function applyName(value) {
        if (editingScope === scopeKey && !editor.inputMethodComposing && enabled)
            submitted(value)
        finish()
    }
    function openNames() {
        editingScope = scopeKey
        filtering = false
        suggestionsList.currentIndex = -1
        suggestionsPopup.open()
    }
    onScopeKeyChanged: {
        if (editor.activeFocus && editingScope !== scopeKey) {
            if (editor.inputMethodComposing) Qt.inputMethod.reset()
            finish()
        }
        if (suggestionsPopup.opened) suggestionsPopup.close()
    }
    RowLayout {
        anchors.fill: parent; anchors.leftMargin: 5; anchors.rightMargin: 3; spacing: 1
        Rectangle { implicitWidth: 7; implicitHeight: 7; radius: 4; visible: !!field.nameValue && !field.mixedCount; color: field.nameColor || Theme.muted }
        TextField {
            id: editor; objectName: field.inputName; Layout.fillWidth: true; Layout.minimumWidth: 8
            background: Item {}
            padding: 2; maximumLength: 80; font.pixelSize: 11; selectByMouse: true; clip: true
            placeholderText: field.mixedCount ? "混合（已选 " + field.mixedCount + " 句）" : field.emptyHint
            placeholderTextColor: Theme.muted; color: Theme.ink
            Binding { target: editor; property: "text"; value: field.mixedCount ? "" : field.nameValue; when: !editor.activeFocus; restoreMode: Binding.RestoreNone }
            onActiveFocusChanged: if (activeFocus) { field.editingScope=field.scopeKey; field.filtering=false; selectAll() }
            onTextEdited: {
                if (inputMethodComposing) return
                field.filtering = true
                suggestionsList.currentIndex = -1
                if (text.trim() && field.suggestions.length) suggestionsPopup.open()
                else suggestionsPopup.close()
            }
            onAccepted: {
                if (inputMethodComposing || !activeFocus || field.editingScope !== field.scopeKey) return
                var i = suggestionsList.currentIndex
                if (suggestionsPopup.opened && i >= 0 && i < field.suggestions.length) field.applyName(field.suggestions[i].name)
                else if (text.trim() || !field.mixedCount && field.nameValue) field.applyName(text.trim())
                else field.finish()
            }
            Keys.onDownPressed: event => {
                if (inputMethodComposing) { event.accepted=false; return }
                if (!suggestionsPopup.opened) field.openNames()
                suggestionsList.currentIndex=Math.min(suggestionsList.currentIndex+1,field.suggestions.length-1)
            }
            Keys.onUpPressed: event => {
                if (inputMethodComposing || !suggestionsPopup.opened) { event.accepted=false; return }
                suggestionsList.currentIndex=Math.max(0,suggestionsList.currentIndex-1)
            }
            Keys.onTabPressed: event => {
                if (!inputMethodComposing && field.filtering && field.suggestions.length) {
                    editor.text=field.suggestions[Math.max(0,suggestionsList.currentIndex)].name
                    suggestionsPopup.close()
                } else event.accepted=false
            }
            Keys.onEscapePressed: event => { if (inputMethodComposing) event.accepted=false; else field.finish() }
        }
        ToolButton {
            text: "×"; implicitWidth: 19; implicitHeight: 24; visible: !!field.nameValue || field.mixedCount>0
            Accessible.name: "清除说话人"; background: Rectangle { color:parent.hovered ? Theme.lilacSoft:"transparent"; radius:4 }
            contentItem: Text { text:parent.text; horizontalAlignment:Text.AlignHCenter; verticalAlignment:Text.AlignVCenter; color:Theme.muted }
            onClicked: { field.editingScope=field.scopeKey; field.applyName("") }
        }
        ToolButton {
            implicitWidth:20; implicitHeight:24; padding:0; Accessible.name:"选择已有说话人"
            background: Rectangle { color:parent.hovered ? Theme.lilacSoft:"transparent"; radius:4 }
            contentItem: Item { Image { anchors.centerIn:parent; width:14; height:14; source:"../assets/icons/chev-down.svg" } }
            onClicked: field.openNames()
        }
    }
    Popup {
        id: suggestionsPopup; objectName: field.menuName
        y: field.height; width: Math.max(150,field.width); height: Math.min(160, Math.max(28,field.suggestions.length*27)+6)
        padding: 3; margins: 4; focus: false
        background: Rectangle { color: Theme.paper; radius: 6; border.color: Theme.line }
        contentItem: ListView {
            id: suggestionsList; model: field.suggestions; clip: true; currentIndex: -1
            ScrollBar.vertical: ScrollBar {}
            delegate: ItemDelegate {
                required property var modelData
                required property int index
                width: suggestionsList.width; height: 27; text: modelData.name; highlighted: index===suggestionsList.currentIndex
                onClicked: field.applyName(modelData.name)
            }
            Label { visible: !field.suggestions.length; anchors.centerIn: parent; text: "暂无已用说话人"; color:Theme.muted; font.pixelSize:11 }
        }
    }
}
