import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.pixel-jack.met-wallpaper"

  readonly property string pluginDir: Quickshell.env("HOME") + "/.config/omarchy/plugins/io.github.pixel-jack.met-wallpaper"

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function open() {
    if (panelLoader.item) panelLoader.item.open()
  }
  function close() {
    if (panelLoader.item) panelLoader.item.close()
  }
  function toggle() {
    if (panelLoader.item) panelLoader.item.toggle()
  }
  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  function injectPanel() {
    if (!panelLoader.item) return
    panelLoader.item.bar = root.bar
    panelLoader.item.anchorItem = button
    panelLoader.item.hostWidget = root
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onBarChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  // Scroll-to-skip without opening the panel. Reuses the panel's own skip()
  // (which also refreshes its list) whenever the panel has been loaded.
  Process {
    id: skipProc
    command: [root.pluginDir + "/bin/omarchy-met-wallpaper-skip"]
    onExited: if (panelLoader.item) panelLoader.item.refresh()
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "🖼"
    tooltipText: "Met Wallpaper — click to browse today's paintings, scroll to skip"
    onPressed: function (buttonCode) {
      if (buttonCode === Qt.LeftButton) root.toggle()
    }
    onWheelMoved: function (delta) {
      if (!skipProc.running) skipProc.running = true
    }
  }
}
