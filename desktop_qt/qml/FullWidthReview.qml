import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Panel {
    id: panel
    objectName: "fullWidthReview"
    required property var controller
    required property var state
    required property var media
    signal closeRequested()
    onVisibleChanged: if (visible) Qt.callLater(function() { rows.positionViewAtIndex(Math.max(0,state.selectedVisibleIndex || 0),ListView.Contain) })
    property bool timecodes: false
    signal timeFormatRequested()
    readonly property var columns: ["句号","确认","入点","出点","时长/秒","字/秒","说话人","样式"]
    function columnWidth(i) { return (width < 1000 ? [30,30,59,59,46,44,66,70] : [38,34,78,78,65,55,95,100])[i] }
    readonly property bool editable: !state.candidateReadOnly && !(state.alignmentPreview || {}).active
    readonly property var current: (state.selection || {}).current || ({})
    function cmd(name, data) { if(name==="review.select") panel.forceActiveFocus(); controller.command(name, data || {}) }
    function editSelectedRow() {
        if(!editable || current.locked)return
        var index=state.selectedVisibleIndex
        if(index<0)return
        rows.positionViewAtIndex(index,ListView.Contain)
        Qt.callLater(function(){var item=rows.itemAtIndex(index);if(item)item.editText()})
    }
    function timestamp(seconds) { if (seconds < 0) return "—"; return Math.floor(seconds/60) + ":" + (seconds%60).toFixed(1).padStart(4,"0") }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 8; spacing: 5
        RowLayout {
            Label { text: "全宽精校"; font.pixelSize: 17; color: Theme.ink }
            Check { objectName: "fullPendingOnly"; text:"待确认"; checked: !!(panel.state.view || {}).unconfirmedOnly; onToggled:panel.cmd("view.filter",{unconfirmedOnly:checked}) }
            ActionButton { objectName:"fullPlayCue"; glyph:"play-cue"; compact:true; tip:"试听本句"; enabled:!!panel.media.ready && panel.current.firstFrame>0; onClicked:panel.cmd("media.playCue") }
            ActionButton { glyph: "plus"; text: "新增"; compact: true; enabled: panel.editable; onClicked: panel.cmd("row.add", panel.current.cueId ? {after: panel.current.cueId} : {}) }
            ActionButton { text:"删除"; compact:true; destructive:true; enabled:panel.editable && !!panel.current.cueId && !panel.current.locked; onClicked:panel.cmd("row.delete",{id:panel.current.cueId}) }
            Item { Layout.fillWidth:true }
            ReviewTaskStatus { controller:panel.controller; state:panel.state; Layout.fillWidth:true; Layout.minimumWidth:145; Layout.preferredWidth:200; Layout.preferredHeight:27 }
            SpeakerField {
                id: fullSpeaker; inputName: "fullSpeakerInput"; menuName: "fullSpeakerMenu"
                Layout.preferredWidth: panel.width < 1050 ? 125 : 165; Layout.minimumWidth: 96
                enabled: panel.editable && !!panel.current.cueId && !panel.current.locked
                property var selected: panel.state.speakerSelection || ({})
                scopeKey: JSON.stringify([(panel.state.project || {}).id,panel.current.cueId,panel.state.multiSelectedIds])
                nameValue: selected.name || ""; nameColor: selected.color || ""; mixedCount: selected.mixed ? selected.count : 0
                choices: panel.state.reviewSpeakers || []
                onSubmitted: value => panel.cmd("review.assignSpeaker", {name:value})
            }
            ChoiceBox {
                id: fullStyle; objectName:"fullStylePicker"; implicitHeight:28; Layout.preferredWidth:105
                enabled:panel.editable && !!panel.current.cueId && !panel.current.locked
                property var styles:(panel.state.reviewStyles || {}).items || []
                model: styles.concat([{name:"管理样式…",id:"@manage"}]); textRole:"name"
                displayText:panel.current.styleName || "默认样式"; currentIndex:styles.findIndex(item => item.id===panel.current.styleId)
                onActivated:index => { if(index===styles.length)panel.cmd("style.manager",{batch:true}); else panel.cmd("review.assignStyle",{id:styles[index].id}) }
            }
            ActionButton { objectName:"fullEditStyle"; text:"编辑"; compact:true; enabled:fullStyle.enabled; onClicked:panel.cmd("style.editCurrent") }
            ActionButton { objectName: "fullReviewTimeFormat"; text: panel.timecodes ? "分秒" : "帧数"; compact: true; onClicked: panel.timeFormatRequested() }
            ActionButton { objectName: "closeFullWidthReview"; text: "返回四窗"; compact: true; onClicked: panel.closeRequested() }
        }
        RowLayout {
            Layout.fillWidth:true; Layout.leftMargin:4; spacing:4
            Repeater { model:panel.columns
                Label { required property int index; required property string modelData; text:modelData
                    Layout.preferredWidth:panel.columnWidth(index); Layout.minimumWidth:Layout.preferredWidth; Layout.maximumWidth:Layout.preferredWidth; font.pixelSize:11; color:Theme.label }
            }
            Label { text:"原文"; Layout.fillWidth:true; Layout.preferredWidth:1; color:Theme.label }
            Label { text:"译文"; Layout.fillWidth:true; Layout.preferredWidth:1; color:Theme.label }
        }
        ListView {
            id: rows; objectName: "fullReviewList"; Layout.fillWidth:true; Layout.fillHeight:true; clip:true; spacing:2
            model: panel.controller ? panel.controller.rows : null
            ScrollBar.vertical: ScrollBar { }
            delegate: Rectangle {
                id: row
                required property int index
                required property string cueId
                required property int number
                required property int firstFrame
                required property int lastFrame
                required property real startSeconds
                required property real endSeconds
                required property real durationSeconds
                required property real cps
                required property bool cpsWarning
                required property string speakerName
                required property string speakerColor
                required property string styleName
                required property string source
                required property string chinese
                required property string translationText
                required property string textRole
                required property bool confirmed
                required property bool locked
                property bool selected: panel.current.cueId===cueId || (panel.state.multiSelectedIds || []).indexOf(cueId)>=0
                function editText() { var editor=textRole==="translation" ? translationEdit : sourceEdit; editor.editing=true; editor.forceActiveFocus() }
                width:rows.width; height:Math.max(42,sourceEdit.contentHeight+12,translationEdit.contentHeight+12)
                color:selected ? Theme.lilacSoft : (index%2 ? Theme.slab : Theme.paper)
                Rectangle { width:3; height:parent.height; color:row.speakerColor || "transparent" }
                MouseArea { anchors.fill:parent; acceptedButtons:Qt.LeftButton|Qt.RightButton
                    onClicked: mouse => { if(mouse.button===Qt.RightButton){ if(!row.selected)panel.cmd("review.select",{id:row.cueId});rowMenu.popup() } else panel.cmd("review.select",{id:row.cueId,ctrl:!!(mouse.modifiers&Qt.ControlModifier),shift:!!(mouse.modifiers&Qt.ShiftModifier)}) }
                }
                RowLayout {
                    anchors.fill:parent; anchors.leftMargin:4; spacing:4
                    Label { text:row.number; Layout.preferredWidth:panel.columnWidth(0); Layout.minimumWidth:Layout.preferredWidth; Layout.maximumWidth:Layout.preferredWidth; color:Theme.label }
                    ToolButton { text: row.confirmed ? "✓" : "○"; Layout.preferredWidth:panel.columnWidth(1); Layout.minimumWidth:Layout.preferredWidth; Layout.maximumWidth:Layout.preferredWidth; enabled:panel.editable && !row.locked; Accessible.name:row.confirmed ? "取消确认":"确认本句"; onClicked:panel.cmd("row.confirm",{id:row.cueId,value:!row.confirmed}) }
                    Label { text:panel.timecodes ? panel.timestamp(row.startSeconds) : (row.firstFrame || "—"); Layout.preferredWidth:panel.columnWidth(2); Layout.minimumWidth:Layout.preferredWidth; Layout.maximumWidth:Layout.preferredWidth; color:Theme.label }
                    Label { text:panel.timecodes ? panel.timestamp(row.endSeconds) : (row.lastFrame || "—"); Layout.preferredWidth:panel.columnWidth(3); Layout.minimumWidth:Layout.preferredWidth; Layout.maximumWidth:Layout.preferredWidth; color:Theme.label }
                    Label { text:row.durationSeconds ? row.durationSeconds.toFixed(2) : "—"; Layout.preferredWidth:panel.columnWidth(4); Layout.minimumWidth:Layout.preferredWidth; Layout.maximumWidth:Layout.preferredWidth; color:Theme.label }
                    Label { text:row.durationSeconds ? row.cps : "—"; Layout.preferredWidth:panel.columnWidth(5); Layout.minimumWidth:Layout.preferredWidth; Layout.maximumWidth:Layout.preferredWidth; color:row.cpsWarning ? Theme.amber : Theme.label }
                    Label { objectName:"fullSpeaker_"+row.cueId; text:row.speakerName; Layout.preferredWidth:panel.columnWidth(6); Layout.minimumWidth:Layout.preferredWidth; Layout.maximumWidth:Layout.preferredWidth; elide:Text.ElideRight; color:row.speakerColor || Theme.label; MouseArea { anchors.fill:parent; enabled:panel.editable; onClicked:mouse=>{if(mouse.modifiers&(Qt.ControlModifier|Qt.ShiftModifier))panel.cmd("review.select",{id:row.cueId,ctrl:!!(mouse.modifiers&Qt.ControlModifier),shift:!!(mouse.modifiers&Qt.ShiftModifier)});else {if(!row.selected)panel.cmd("review.select",{id:row.cueId});fullSpeaker.openNames()}} } }
                    Label { objectName:"fullStyle_"+row.cueId; text:row.styleName; Layout.preferredWidth:panel.columnWidth(7); Layout.minimumWidth:Layout.preferredWidth; Layout.maximumWidth:Layout.preferredWidth; elide:Text.ElideRight; color:Theme.label; MouseArea { anchors.fill:parent; enabled:panel.editable; onClicked:mouse=>{if(mouse.modifiers&(Qt.ControlModifier|Qt.ShiftModifier))panel.cmd("review.select",{id:row.cueId,ctrl:!!(mouse.modifiers&Qt.ControlModifier),shift:!!(mouse.modifiers&Qt.ShiftModifier)});else{if(!row.selected)panel.cmd("review.select",{id:row.cueId});fullStyle.popup.open()}} } }
                    TextArea {
                        id:sourceEdit; objectName:"fullSource_"+row.cueId; Layout.fillWidth:true; Layout.preferredWidth:1; Layout.fillHeight:true
                        property bool editing:false
                        Binding { target:sourceEdit; property:"text"; value:row.textRole!=="translation" ? row.chinese : row.source; when:!sourceEdit.activeFocus && !sourceEdit.inputMethodComposing; restoreMode:Binding.RestoreNone }
                        Component.onCompleted: text = row.textRole!=="translation" ? row.chinese : row.source
                        wrapMode:TextEdit.Wrap; padding:4; color:row.locked || !panel.editable || row.textRole==="translation" ? Theme.muted : Theme.text; textFormat:TextEdit.PlainText
                        readOnly:!editing || !panel.editable || row.locked || row.textRole==="translation"; activeFocusOnPress:editing
                        background:Rectangle { color:"transparent" }
                        onActiveFocusChanged: { panel.cmd("editor.activity",{id:row.cueId,active:activeFocus,composing:inputMethodComposing});if(!activeFocus)editing=false }
                        onInputMethodComposingChanged: { panel.cmd("editor.activity",{id:row.cueId,active:activeFocus,composing:inputMethodComposing}); if (!inputMethodComposing) commit() }
                        function commit() { if(activeFocus && !readOnly && !inputMethodComposing && text!==row.chinese)panel.cmd("row.edit",{id:row.cueId,text:text}) }
                        onTextChanged:commit()
                        Keys.onEscapePressed:{ editing=false;panel.forceActiveFocus() }
                        MouseArea { anchors.fill:parent; z:10; acceptedButtons:Qt.RightButton
                            onClicked: { if (!row.selected) panel.cmd("review.select",{id:row.cueId});rowMenu.popup() }
                        }
                        MouseArea { anchors.fill:parent; enabled:!sourceEdit.editing
                            onClicked:mouse=>panel.cmd("review.select",{id:row.cueId,ctrl:!!(mouse.modifiers&Qt.ControlModifier),shift:!!(mouse.modifiers&Qt.ShiftModifier)})
                            onDoubleClicked:if(panel.editable && !row.locked && row.textRole!=="translation"){sourceEdit.editing=true;sourceEdit.forceActiveFocus()}
                        }
                    }
                    TextArea {
                        id:translationEdit; objectName:"fullTranslation_"+row.cueId; Layout.fillWidth:true; Layout.preferredWidth:1; Layout.fillHeight:true
                        property bool editing:false
                        Binding { target:translationEdit; property:"text"; value:row.textRole==="translation" ? row.chinese : row.translationText; when:!translationEdit.activeFocus && !translationEdit.inputMethodComposing; restoreMode:Binding.RestoreNone }
                        Component.onCompleted: text = row.textRole==="translation" ? row.chinese : row.translationText
                        wrapMode:TextEdit.Wrap; padding:4; color:Theme.text; textFormat:TextEdit.PlainText
                        readOnly:!editing || !panel.editable || row.locked || row.textRole!=="translation"; activeFocusOnPress:editing
                        background:Rectangle { color:"transparent" }
                        onActiveFocusChanged:{panel.cmd("editor.activity",{id:row.cueId,active:activeFocus,composing:inputMethodComposing});if(!activeFocus)editing=false}
                        onInputMethodComposingChanged: { panel.cmd("editor.activity",{id:row.cueId,active:activeFocus,composing:inputMethodComposing}); if (!inputMethodComposing) commit() }
                        function commit() { if(activeFocus && !readOnly && !inputMethodComposing && text!==row.chinese)panel.cmd("row.edit",{id:row.cueId,text:text}) }
                        onTextChanged:commit()
                        Keys.onEscapePressed:{editing=false;panel.forceActiveFocus()}
                        MouseArea { anchors.fill:parent; z:10; acceptedButtons:Qt.RightButton
                            onClicked: { if (!row.selected) panel.cmd("review.select",{id:row.cueId});rowMenu.popup() }
                        }
                        MouseArea { anchors.fill:parent; enabled:!translationEdit.editing
                            onClicked:mouse=>panel.cmd("review.select",{id:row.cueId,ctrl:!!(mouse.modifiers&Qt.ControlModifier),shift:!!(mouse.modifiers&Qt.ShiftModifier)})
                            onDoubleClicked:if(panel.editable && !row.locked && row.textRole==="translation"){translationEdit.editing=true;translationEdit.forceActiveFocus()}
                        }
                    }
                }
                Menu { id:rowMenu
                    MenuItem { text:"选中全部同说话人句子"; enabled:panel.editable; onTriggered:panel.cmd("review.selectSameSpeaker",{id:row.cueId}) }
                    MenuItem { text:"合并选中"; enabled:panel.editable && (panel.state.multiSelectedIds || []).length>=2; onTriggered:panel.cmd("row.merge",{id:row.cueId,ids:panel.state.multiSelectedIds}) }
Menu { title: "指定说话人"; enabled: panel.editable
                                    MenuItem { text: "清除说话人"; onTriggered: panel.cmd("review.assignSpeaker",{}) }
                                    Repeater { model: panel.state.reviewSpeakers || []; MenuItem { required property var modelData; text: modelData.name; onTriggered: panel.cmd("review.assignSpeaker",{id:modelData.id}) } }
                                }
                                Menu { title: "指定样式"; enabled: panel.editable
                                    Repeater { model: (panel.state.reviewStyles || {}).items || []; MenuItem { required property var modelData; text: modelData.name; onTriggered: panel.cmd("review.assignStyle",{id:modelData.id}) } }
                                }
                }
            }
        }
    }
}
