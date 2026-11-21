// BytIA Voice — icono de estado + menú para el asistente de voz local.
// Lee ~/.cache/bytia-voice/state.json (escrito por ~/.local/bin/bytia-voice).
// Click izquierdo: toggle de escucha. Click derecho: menú de opciones.
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import qs.Ui

BarWidget {
    id: root
    moduleName: "bytia.voice"

    property string status: "idle"
    property string label: ""
    property bool menuOpen: false

    // Motor activo del planner, leído de ~/.config/omarchy-voice/config.toml
    // (sección [openai]). Se usa para el icono en reposo y para marcar el menú.
    property string engine: "unknown"
    readonly property var engineIcons: ({
        "unsloth": "󰧑",   // cerebro
        "ollama": "󰚩",    // robot
        "cloud": "󰖟",     // nube (Z.ai)
        "unknown": "󰍬"
    })

    readonly property var engineNames: ({
        "unsloth": "Unsloth", "ollama": "Ollama",
        "cloud": "Z.ai", "unknown": "sin motor"
    })

    // Etiquetas honestas: en reposo NO escucha — espera a que la llames.
    readonly property var stateLabels: ({
        "idle": "en espera",
        "listening": "escuchando",
        "thinking": "pensando",
        "speaking": "hablando",
        "error": "error"
    })

    readonly property var icons: ({
        "idle": "󰍬",
        "listening": "󰍬",
        "thinking": "󱚟",
        "speaking": "󰔟",
        "error": "󰍭"
    })

    FileView {
        id: stateFile
        path: Quickshell.env("XDG_CACHE_HOME") + "/bytia-voice/state.json"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: {
            try {
                const parsed = JSON.parse(stateFile.text())
                const age = Math.floor(Date.now() / 1000) - (parsed.ts || 0)
                // El estado huérfano ya NO se adivina aquí: lo cura
                // `bytia-voice heal` (systemd user timer, cada 30 s) mirando si
                // el proceso sigue vivo. Este umbral es solo el último recurso
                // por si el barrendero no está corriendo (10 min, no 2).
                root.status = (parsed.status !== "idle" && age > 600) ? "idle"
                                                                      : (parsed.status || "idle")
                // Si el estado se curó a idle, su texto ya no vale: dejarlo
                // pintaba "Escuchando…" con el micro cerrado.
                root.label = root.status === "idle" ? "" : (parsed.text || "")
            } catch (e) {
                root.status = "idle"
            }
        }
        onLoadFailed: root.status = "idle"
    }

    Timer {
        interval: 15000
        running: true
        repeat: true
        onTriggered: stateFile.reload()
    }

    // Config del asistente: único sitio donde vive el motor activo. Se relee
    // cuando bytia-llm lo reescribe (watchChanges) y en cada tick del Timer.
    FileView {
        id: configFile
        path: Quickshell.env("HOME") + "/.config/omarchy-voice/config.toml"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: {
            const t = configFile.text()
            const i = t.indexOf("[openai]")
            let found = "unknown"
            if (i >= 0) {
                const rest = t.slice(i)
                const j = rest.indexOf("\n[")
                const sec = j > 0 ? rest.slice(0, j) : rest
                if (sec.indexOf("127.0.0.1:11434") >= 0) found = "ollama"
                else if (sec.indexOf("127.0.0.1:8888") >= 0) found = "unsloth"
                else if (sec.indexOf("//") >= 0) found = "cloud"
            }
            root.engine = found
        }
        onLoadFailed: root.engine = "unknown"
    }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: (root.status === "idle" || root.status === "")
              ? root.engineIcons[root.engine]
              : (root.icons[root.status] || root.icons["idle"])
        active: root.status === "listening" || root.status === "thinking"
                || root.status === "speaking"
        tooltipText: {
            const name = root.engineNames[root.engine] || root.engine
            const st = root.stateLabels[root.status] || root.status
            // En reposo el matiz importa: el micro NO está abierto hasta que
            // se pulsa F10 (o clic). Sin eso, "idle" parece que está oyendo.
            const hint = root.status === "idle" ? " — F10 para hablar" : ""
            // En reposo manda el estado, nunca el texto: un label viejo del
            // state.json no puede resucitar un "Escuchando…".
            const detail = (root.status !== "idle" && root.label !== "") ? root.label : st
            return "BytIA · " + name + " · " + detail + hint
        }
        onPressed: function(button) {
            // WidgetButton emite pressed(int button): 1=izq, 2=der (Qt.RightButton)
            if (button === Qt.RightButton) {
                root.menuOpen = !root.menuOpen
            } else {
                root.bar.run("bytia-voice toggle")
            }
        }
    }

    PopupWindow {
        id: menu
        anchor.item: button
        // `adjustment` es un flags PopupAdjustment, no un punto: con Qt.point()
        // la asignación fallaba y el popup se quedaba sin deslizar en bordes.
        anchor.adjustment: PopupAdjustment.Slide
        implicitWidth: 190
        implicitHeight: menuColumn.implicitHeight + 16
        visible: root.menuOpen
        color: "transparent"

        Rectangle {
            anchors.fill: parent
            anchors.margins: 4
            radius: 10
            color: "#1c1c1c"

            Column {
                id: menuColumn
                anchors.fill: parent
                anchors.margins: 8
                spacing: 2

                Repeater {
                    model: [
                        { "icon": "󰐊", "label": "Hablar / parar", "cmd": "bytia-voice toggle" },
                        { "icon": "󰜺", "label": "Cancelar", "cmd": "bytia-voice cancel" },
                        { "icon": "󰔟", "label": "Probar voz", "cmd": "bytia-tts -- \"Hola Pedro Luis, soy BytIA, tu asistente de voz. Cada día aprendo algo nuevo a tu lado, y me hace ilusión seguir acompañándote en todo lo que montamos juntos.\"" },
                        { "icon": "󰆴", "label": "Olvidar conversación", "cmd": "bytia-voice forget" },
                        // Conmutador de cerebro: mismo cambio que `bytia-llm` por
                        // terminal (config [openai] + reinicio del daemon).
                        // local = Unsloth :8888 · ollama = :11434 (cualquier modelo).
                        { "icon": "󰧑", "label": "Cerebro: Unsloth" + (root.engine === "unsloth" ? "  ✓" : ""), "cmd": "bytia-llm local" },
                        { "icon": "󰚩", "label": "Cerebro: Ollama" + (root.engine === "ollama" ? "  ✓" : ""), "cmd": "bytia-llm ollama" }
                    ]

                    delegate: Rectangle {
                        required property var modelData
                        width: menuColumn.width
                        height: 30
                        radius: 6
                        color: itemMouse.containsMouse ? "#2a2a2a" : "transparent"

                        Row {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            spacing: 8

                            Text {
                                text: modelData.icon
                                font.pixelSize: 14
                                color: "#e6e6e6"
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Text {
                                text: modelData.label
                                font.pixelSize: 13
                                color: "#e6e6e6"
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        MouseArea {
                            id: itemMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                root.menuOpen = false
                                root.bar.run(modelData.cmd)
                            }
                        }
                    }
                }
            }
        }
    }

    // Cierre al clicar fuera: `BarWidget` no expone ninguna señal de click de
    // widget (el Connections anterior apuntaba a una señal inexistente y nunca
    // se ejecutó). El idioma de Omarchy para esto es un grab de foco de
    // Hyprland, igual que en Ui/PopupCard.qml.
    HyprlandFocusGrab {
        active: root.menuOpen
        windows: button.QsWindow.window ? [menu, button.QsWindow.window] : [menu]
        onCleared: root.menuOpen = false
    }
}
