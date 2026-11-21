import QtQuick
import QtQuick.Controls
Rectangle {
    id: badge
    property string text: ""
    property string glyph: ""
    property color foreground: Theme.label
    property color fill: Theme.slab
    property real maximumWidth: 100
    implicitWidth: Math.min(maximumWidth,label.implicitWidth+12+(glyph ? 16 : 0))
    implicitHeight: 23; radius: 6; color: fill
    Label { id: label; anchors.fill:parent; anchors.leftMargin:badge.glyph ? 22 : 6; anchors.rightMargin:6; text:badge.text; textFormat:Text.PlainText; elide:Text.ElideRight; verticalAlignment:Text.AlignVCenter; color:badge.foreground; font.pixelSize:11 }
    Image { visible: !!badge.glyph; source: badge.glyph ? "../assets/icons/"+badge.glyph+".svg" : ""; width:12; height:12; anchors.left:parent.left; anchors.leftMargin:6; anchors.verticalCenter:parent.verticalCenter }
    HoverHandler { id: hover }
    ToolTip.visible:hover.hovered
    ToolTip.text:text
}
