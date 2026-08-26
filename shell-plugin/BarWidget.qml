import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar icon for Quill. Click it to open the edit menu for whatever text is
// selected, the same way the Dictation indicator fronts voxtype.
//
// The glyph flips while a model request is in flight. Quill writes that state
// to $XDG_RUNTIME_DIR/quill/state.json with an atomic rename, so FileView can
// watch it directly instead of us babysitting a follower process.
BarWidget {
  id: root
  moduleName: "quill.writer"

  readonly property string idleIcon: "󰴓"
  readonly property string workingIcon: ""

  readonly property string command: String(setting("command", "quill menu"))
  readonly property string settingsCommand: String(setting("settingsCommand", "quill settings"))
  readonly property bool alwaysShow: setting("alwaysShow", true) === true

  property string state: "idle"
  property string detail: ""
  readonly property bool working: state === "working"

  visible: alwaysShow || working
  implicitWidth: visible ? button.implicitWidth : 0
  implicitHeight: visible ? button.implicitHeight : 0

  function parse(content) {
    var text = String(content || "").trim()
    if (text === "") { root.state = "idle"; root.detail = ""; return }
    try {
      var data = JSON.parse(text)
      root.state = String(data.state || "idle")
      root.detail = String(data.detail || "")
    } catch (error) {
      // A torn read should not strand the icon in a spinner.
      root.state = "idle"
      root.detail = ""
    }
  }

  FileView {
    path: Quickshell.env("XDG_RUNTIME_DIR") + "/quill/state.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.parse(text())
    // Absent file is the normal case before Quill has ever run.
    onLoadFailed: { root.state = "idle"; root.detail = "" }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.working ? root.workingIcon : root.idleIcon
    slotSize: Style.bar.statusSlot
    fontSize: Style.font.caption
    tooltipText: root.working
      ? (root.detail !== "" ? "Quill: " + root.detail + "\u2026" : "Quill: working\u2026")
      : "Quill  \u00b7  click for settings, right-click to edit selection"
    // WidgetButton emits pressed(int button). Left-click opens settings and
    // right-click runs the edit menu, which is the way round the keybinding
    // makes sensible: SUPER+I already covers editing from the keyboard.
    onPressed: function(button) {
      if (!root.bar) return
      root.bar.run(button === Qt.RightButton ? root.command : root.settingsCommand)
    }

    // Spin the glyph while a model request is in flight. WidgetButton exposes
    // textRotation for exactly this, so no extra item is needed.
    NumberAnimation {
      target: button
      property: "textRotation"
      from: 0
      to: 360
      duration: 1100
      loops: Animation.Infinite
      running: root.working
      // Leaving it mid-turn would tilt the idle pen nib.
      onRunningChanged: if (!running) button.textRotation = 0
    }
  }
}
