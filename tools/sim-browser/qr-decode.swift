import Foundation
import ImageIO
import Vision

// This helper emits one QR payload to stdout for its parent process only.
// It deliberately does not log the payload or write it to disk.
guard CommandLine.arguments.count == 2 else {
  fputs("usage: qr-decode.swift IMAGE_PATH\n", stderr)
  exit(2)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
  fputs("unable to load QR image\n", stderr)
  exit(2)
}

let request = VNDetectBarcodesRequest()
request.symbologies = [.qr]
let handler = VNImageRequestHandler(cgImage: image, options: [:])
do {
  try handler.perform([request])
} catch {
  fputs("QR detection failed\n", stderr)
  exit(2)
}

guard let value = request.results?.first?.payloadStringValue, !value.isEmpty else {
  fputs("no QR code detected\n", stderr)
  exit(3)
}
print(value)
