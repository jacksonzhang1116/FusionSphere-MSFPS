const { SerialPort } = require("serialport");
const { ReadlineParser } = require("@serialport/parser-readline");
const WebSocket = require("ws");

// ====== 1) 改成你的串口端口名 ======
// Windows: "COM5"
// macOS: "/dev/tty.usbserial-xxxx" or "/dev/tty.usbmodemxxxx"
// Linux: "/dev/ttyUSB0" or "/dev/ttyACM0"
const SERIAL_PORT = "COM10";

// ====== 2) 改成你的波特率（与 K230 输出一致） ======
const BAUD_RATE = 115200;

// WebSocket 端口
const WS_PORT = 8080;

// 打开串口
const port = new SerialPort({ path: SERIAL_PORT, baudRate: BAUD_RATE });
const parser = port.pipe(new ReadlineParser({ delimiter: "\n" }));

port.on("open", () => {
  console.log(`Serial opened: ${SERIAL_PORT} @ ${BAUD_RATE}`);
});

port.on("error", (err) => {
  console.error("Serial error:", err.message);
});

// 启 WebSocket
const wss = new WebSocket.Server({ port: WS_PORT });
console.log(`WebSocket server: ws://localhost:${WS_PORT}`);

// 串口每来一行：如果是 JSON，就广播给所有浏览器
parser.on("data", (line) => {
  const s = line.trim();
  if (!s.startsWith("{")) return;

  try {
    const obj = JSON.parse(s);
    const msg = JSON.stringify(obj);
    wss.clients.forEach((c) => {
      if (c.readyState === WebSocket.OPEN) c.send(msg);
    });
  } catch (e) {
    // 如果你想调试坏 JSON，把下一行打开：
    // console.log("Bad JSON:", s);
  }
  

});
