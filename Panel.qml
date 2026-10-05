import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "io.github.pixel-jack.met-wallpaper"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property string pluginDir: Quickshell.env("HOME") + "/.config/omarchy/plugins/io.github.pixel-jack.met-wallpaper"
  property var items: []
  property bool paused: false

  function open() {
    root.controller.show()
    root.refresh()
  }
  function close() {
    root.controller.hide()
  }
  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.hostWidget || root, direction)
    return false
  }

  function refresh() {
    if (!listProc.running) listProc.running = true
  }

  function skip() {
    if (!skipProc.running) skipProc.running = true
  }

  function use(hour) {
    useProc.command = [root.pluginDir + "/bin/omarchy-met-wallpaper-use", String(hour)]
    useProc.running = true
  }

  function togglePause() {
    if (!togglePauseProc.running) togglePauseProc.running = true
  }

  onOpenedChanged: if (opened) refresh()

  Process {
    id: listProc
    command: [root.pluginDir + "/bin/omarchy-met-wallpaper-list"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(String(text || "{}"))
          root.items = parsed.items || []
          root.paused = parsed.paused === true
        } catch (e) {
          root.items = []
        }
      }
    }
  }

  Process {
    id: skipProc
    command: [root.pluginDir + "/bin/omarchy-met-wallpaper-skip"]
    onExited: root.refresh()
  }

  Process {
    id: useProc
    onExited: root.refresh()
  }

  Process {
    id: togglePauseProc
    command: [root.pluginDir + "/bin/omarchy-met-wallpaper-toggle-pause"]
    onExited: root.refresh()
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(Style.space(440))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }

      Column {
        anchors.fill: parent
        spacing: Style.space(10)

        Item {
          id: header
          width: parent.width
          height: Math.max(titleText.implicitHeight, skipButton.implicitHeight, pauseButton.implicitHeight)

          Text {
            id: titleText
            textFormat: Text.PlainText
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "Today's Paintings"
            color: root.barForeground
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }

          PanelActionButton {
            id: skipButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            iconText: "⏭"
            tooltipText: "Skip to another painting"
            foreground: root.barForeground
            onClicked: root.skip()
          }

          PanelActionButton {
            id: pauseButton
            anchors.right: skipButton.left
            anchors.rightMargin: Style.space(4)
            anchors.verticalCenter: parent.verticalCenter
            iconText: root.paused ? "▶" : "⏸"
            tooltipText: root.paused ? "Resume automatic rotation" : "Pause automatic rotation"
            foreground: root.barForeground
            onClicked: root.togglePause()
          }
        }

        Flickable {
          id: flick
          width: parent.width
          height: parent.height - header.height - Style.space(10)
          clip: true
          contentWidth: width
          contentHeight: grid.implicitHeight
          boundsBehavior: Flickable.StopAtBounds

          Grid {
            id: grid
            width: flick.width
            columns: 3
            spacing: Style.space(6)

            Repeater {
              model: root.items

              delegate: Rectangle {
                id: cell
                required property var modelData

                width: (grid.width - grid.spacing * (grid.columns - 1)) / grid.columns
                height: width * 2 / 3
                color: "transparent"
                radius: Style.cornerRadius
                border.width: modelData.active ? 3 : 1
                border.color: modelData.active ? Color.accent : Color.popups.border

                Image {
                  anchors.fill: parent
                  anchors.margins: cell.border.width + 1
                  source: Util.fileUrl(cell.modelData.file)
                  fillMode: Image.PreserveAspectCrop
                  asynchronous: true
                  cache: true
                  smooth: true
                }

                Text {
                  textFormat: Text.PlainText
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.bottom: parent.bottom
                  anchors.margins: Style.space(4)
                  text: (cell.modelData.hour < 10 ? "0" : "") + cell.modelData.hour + ":00"
                  color: "white"
                  style: Text.Outline
                  styleColor: "black"
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }

                MouseArea {
                  id: cellMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.use(cell.modelData.hour)
                }

                PanelToolTip {
                  visible: cellMouse.containsMouse
                  text: cell.modelData.title + " — " + cell.modelData.artist
                }
              }
            }
          }
        }
      }
    }
  }
}
