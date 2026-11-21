pragma Singleton
import QtQuick

QtObject {
    property bool motionEnabled: true
    readonly property color canvas: "#F7F5FB"
    readonly property color paper: "#FFFFFF"
    readonly property color slab: "#F8F6FC"
    readonly property color lilac: "#8562C5"
    readonly property color lilacDark: "#63419C"
    readonly property color emphasis: "#E7DDF7"
    readonly property color emphasisHover: "#DED0F1"
    readonly property color emphasisPressed: "#D5C3EC"
    readonly property color emphasisBorder: "#CBB5E7"
    readonly property color lilacSoft: "#F1ECFB"
    readonly property color ink: "#29213A"
    readonly property color text: "#2D263B"
    readonly property color label: "#736985"
    readonly property color muted: "#8C829E"
    readonly property color faint: "#ACA3BC"
    readonly property color line: "#EAE5F3"
    readonly property color lineSoft: "#F1EDF8"
    readonly property color green: "#4E947D"
    readonly property color greenBg: "#EAF6F1"
    readonly property color amber: "#AD7A3B"
    readonly property color amberBg: "#FDF4E7"
    readonly property color pendingTextBg: "#F5DEB7"
    readonly property color pendingTextAccent: "#D99A42"
    readonly property color rose: "#C96F98"
    readonly property color roseBg: "#FCF0F5"
    readonly property string fontFamily: Qt.platform.os === "windows" ? "Microsoft YaHei UI" : "Noto Sans CJK SC"
    function regionColor(index) { return ["#C1A7F2", "#9BCBC0", "#DAABBD", "#DABB8D"][index % 4] }
}
