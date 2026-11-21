import QtQuick
import QtQuick.Controls

Item {
    id: overlay
    required property var regions
    required property var controller
    required property bool editable
    Repeater {
        model: overlay.regions
        delegate: Rectangle {
            id: box
            required property var modelData
            required property int index
            property var draft: null
            readonly property var area: draft || modelData
            readonly property color ink: Theme.regionColor(modelData.paletteIndex || 0)
            objectName: "ocrRegion_"+modelData.id
            x: area.x*overlay.width; y: area.y*overlay.height
            width: area.width*overlay.width; height: area.height*overlay.height
            opacity: modelData.enabled === false ? .4 : 1
            radius: 4
            color: drag.pressed ? "#268562C5" : Qt.rgba(ink.r, ink.g, ink.b, .07)
            border.color: "#DECFFE"; border.width: drag.pressed ? 2 : 0
            Canvas {
                anchors.fill: parent
                visible: !drag.pressed
                property color ink: box.ink
                onInkChanged: requestPaint()
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset(); ctx.lineWidth = 1; ctx.strokeStyle = ink; ctx.setLineDash([5, 4])
                    ctx.beginPath(); ctx.roundedRect(.5,.5,Math.max(0,width-1),Math.max(0,height-1),4,4); ctx.stroke()
                    ctx.setLineDash([]); ctx.lineWidth = 2
                    for (var i=0; i<4; i++) {
                        var right = i % 2, bottom = i >= 2
                        var x = right ? width-1 : 1, y = bottom ? height-1 : 1
                        ctx.beginPath(); ctx.moveTo(x+(right?-1:1)*Math.min(11,width/2),y)
                        ctx.lineTo(x,y); ctx.lineTo(x,y+(bottom?-1:1)*Math.min(11,height/2)); ctx.stroke()
                    }
                }
            }
            MouseArea {
                id: drag
                anchors.fill: parent
                enabled: overlay.editable
                preventStealing: true
                property point origin
                property var initial
                property int horizontalEdge
                property int verticalEdge
                function edge(p, extent) { return p < Math.min(8,extent/3) ? -1 : p > extent - Math.min(8,extent/3) ? 1 : 0 }
                onPressed: function(m) {
                    origin=mapToItem(overlay,m.x,m.y)
                    initial=box.modelData
                    horizontalEdge=edge(m.x,width);verticalEdge=edge(m.y,height)
                }
                onPositionChanged: function(m) {
                    if (!pressed || !initial) return
                    var pos=mapToItem(overlay,m.x,m.y), dx=(pos.x-origin.x)/overlay.width, dy=(pos.y-origin.y)/overlay.height
                    var a=initial, x=a.x,y=a.y,u=x+a.width,v=y+a.height
                    if (!horizontalEdge && !verticalEdge) {
                        x=Math.max(0,Math.min(1-a.width,x+dx)); y=Math.max(0,Math.min(1-a.height,y+dy))
                        u=x+a.width;v=y+a.height
                    } else {
                        if (horizontalEdge<0) x=Math.max(0,Math.min(u-2/overlay.width,x+dx))
                        if (horizontalEdge>0) u=Math.min(1,Math.max(x+2/overlay.width,u+dx))
                        if (verticalEdge<0) y=Math.max(0,Math.min(v-2/overlay.height,y+dy))
                        if (verticalEdge>0) v=Math.min(1,Math.max(y+2/overlay.height,v+dy))
                    }
                    box.draft={x:x,y:y,width:u-x,height:v-y}
                }
                onCanceled: { box.draft=null;initial=null }
                onReleased: {
                    var a=box.draft
                    box.draft=null;initial=null
                    if (a) overlay.controller.command("roi.multi",{action:"geometry",id:box.modelData.id,box:[a.x,a.y,a.x+a.width,a.y+a.height]})
                }
            }
            Rectangle {
                objectName: "regionTag_" + box.modelData.id
                x: Math.max(-box.x,Math.min(0,overlay.width-box.x-width))
                y: box.y >= height + 2 || box.y+box.height+height+2 > overlay.height ? -height - 2 : box.height + 2
                radius: 8; color: Qt.tint(Theme.paper, Qt.rgba(box.ink.r,box.ink.g,box.ink.b,.35)); height: 19
                width: Math.min(overlay.width,tag.implicitWidth+27)
                clip: true
                Text { id: tag; x:4; anchors.verticalCenter: parent.verticalCenter; width:Math.max(0,parent.width-22); text: box.modelData.label; color: Theme.ink; font.pixelSize: 10; elide:Text.ElideRight }
                Text { anchors.right:parent.right; width:17; height:parent.height; text:"×"; color:Theme.ink; horizontalAlignment:Text.AlignHCenter; visible:overlay.editable
                    MouseArea { anchors.fill:parent; onClicked:overlay.controller.command("roi.multi",{action:"remove",id:box.modelData.id}) }
                }
            }
        }
    }
}
