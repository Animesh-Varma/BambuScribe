import socket
import ssl
from backend.config import PRINTER_IP, ACCESS_CODE

def generate_bambu_camera_stream():
    """Connect to Bambu Lab stream (6000), decode MJPEG, and yield frames."""
    auth_packet = bytearray(
        [0x40, 0x00, 0x00, 0x00, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    auth_packet += "bblp".encode('utf-8').ljust(32, b'\x00') + ACCESS_CODE.encode('utf-8').ljust(32, b'\x00')
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        sock = socket.create_connection((PRINTER_IP, 6000), timeout=5)
        secure_sock = ctx.wrap_socket(sock, server_hostname=PRINTER_IP)
        secure_sock.sendall(auth_packet)
        buffer = b''
        while True:
            chunk = secure_sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > 5000000: buffer = b''
            while True:
                start_idx, end_idx = buffer.find(b'\xff\xd8'), buffer.find(b'\xff\xd9')
                if start_idx != -1 and end_idx != -1:
                    if start_idx < end_idx:
                        jpg = buffer[start_idx:end_idx + 2]
                        buffer = buffer[end_idx + 2:]
                        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpg + b'\r\n')
                    else:
                        buffer = buffer[end_idx + 2:]
                else:
                    break
    except Exception:
        pass