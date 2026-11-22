import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import { localObjectPath, usingS3 } from "@/lib/catalog"
import { dataRoot } from "@/lib/runs"

export const runtime = "nodejs"

function safeKey(parts: string[]): string | null {
  const key = parts.map((p) => decodeURIComponent(p)).join("/")
  if (!key || key.includes("..")) return null
  return key.replace(/^\/+/, "")
}

function contentTypeFor(key: string): string {
  const ext = path.extname(key).toLowerCase()
  return (
    {
      ".mp4": "video/mp4",
      ".webm": "video/webm",
      ".mov": "video/quicktime",
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".png": "image/png",
      ".webp": "image/webp",
    }[ext] || "application/octet-stream"
  )
}

function s3Endpoint(): string | undefined {
  return (
    process.env.AWS_ENDPOINT_URL_S3 ||
    process.env.AWS_S3_ENDPOINT_URL ||
    undefined
  )
}

function s3ForcePathStyle(): boolean {
  const explicit = (process.env.AWS_S3_ADDRESSING_STYLE || "").trim().toLowerCase()
  if (explicit === "path") return true
  if (explicit === "virtual") return false
  const ep = (s3Endpoint() || "").toLowerCase()
  return (
    ep.includes("localhost") ||
    ep.includes("127.0.0.1") ||
    ep.includes("0.0.0.0") ||
    ep.includes("minio") ||
    ep.includes(":9000")
  )
}

/** Clamp HTTP Range to [0, size-1]. Returns null if unsatisfiable. */
function clampByteRange(
  rangeHeader: string,
  size: number
): { start: number; end: number } | null {
  if (size <= 0) return null
  const m = /bytes=(\d*)-(\d*)/.exec(rangeHeader)
  if (!m) return null
  let start = m[1] ? parseInt(m[1], 10) : 0
  let end = m[2] ? parseInt(m[2], 10) : size - 1
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0) return null
  if (start >= size) return null
  end = Math.min(end, size - 1)
  if (end < start) return null
  return { start, end }
}

async function s3Range(
  key: string,
  rangeHeader: string | null
): Promise<NextResponse> {
  const { S3Client, GetObjectCommand, HeadObjectCommand } = await import(
    "@aws-sdk/client-s3"
  )
  const client = new S3Client({
    region: process.env.AWS_DEFAULT_REGION || "us-east-1",
    endpoint: s3Endpoint(),
    forcePathStyle: s3ForcePathStyle(),
    credentials: {
      accessKeyId: process.env.AWS_ACCESS_KEY_ID || "",
      secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY || "",
    },
  })
  const bucket = process.env.AWS_S3_BUCKET_NAME || ""
  const head = await client.send(
    new HeadObjectCommand({ Bucket: bucket, Key: key })
  )
  const size = Number(head.ContentLength || 0)
  const type = head.ContentType || contentTypeFor(key)

  if (rangeHeader) {
    const clamped = clampByteRange(rangeHeader, size)
    if (!clamped) {
      return new NextResponse(null, {
        status: 416,
        headers: {
          "Content-Range": `bytes */${size}`,
          "Accept-Ranges": "bytes",
        },
      })
    }
    const { start, end } = clamped
    const resp = await client.send(
      new GetObjectCommand({
        Bucket: bucket,
        Key: key,
        Range: `bytes=${start}-${end}`,
      })
    )
    const bytes = Buffer.from(await resp.Body!.transformToByteArray())
    return new NextResponse(bytes, {
      status: 206,
      headers: {
        "Content-Type": type,
        "Content-Length": String(bytes.length),
        "Content-Range": `bytes ${start}-${end}/${size}`,
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=60",
      },
    })
  }

  const resp = await client.send(
    new GetObjectCommand({ Bucket: bucket, Key: key })
  )
  const bytes = Buffer.from(await resp.Body!.transformToByteArray())
  return new NextResponse(bytes, {
    status: 200,
    headers: {
      "Content-Type": type,
      "Content-Length": String(bytes.length),
      "Accept-Ranges": "bytes",
      "Cache-Control": "private, max-age=60",
    },
  })
}

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ key: string[] }> }
) {
  const { key: parts } = await ctx.params
  const key = safeKey(parts)
  if (!key) {
    return NextResponse.json({ error: "bad key" }, { status: 400 })
  }

  try {
    if (usingS3()) {
      return await s3Range(key, req.headers.get("range"))
    }

    const filePath = localObjectPath(key)
    const root = path.resolve(dataRoot(), "objects")
    if (!filePath.startsWith(root + path.sep) && filePath !== root) {
      return NextResponse.json({ error: "forbidden" }, { status: 403 })
    }
    if (!fs.existsSync(filePath)) {
      return NextResponse.json({ error: "not found" }, { status: 404 })
    }
    const stat = fs.statSync(filePath)
    const size = stat.size
    const type = contentTypeFor(key)
    const range = req.headers.get("range")

    if (range) {
      const clamped = clampByteRange(range, size)
      if (!clamped) {
        return new NextResponse(null, {
          status: 416,
          headers: {
            "Content-Range": `bytes */${size}`,
            "Accept-Ranges": "bytes",
          },
        })
      }
      const { start, end } = clamped
      const length = end - start + 1
      const chunk = Buffer.alloc(length)
      const fd = fs.openSync(filePath, "r")
      const read = fs.readSync(fd, chunk, 0, length, start)
      fs.closeSync(fd)
      const body = read < length ? chunk.subarray(0, read) : chunk
      return new NextResponse(body, {
        status: 206,
        headers: {
          "Content-Type": type,
          "Content-Length": String(body.length),
          "Content-Range": `bytes ${start}-${start + body.length - 1}/${size}`,
          "Accept-Ranges": "bytes",
          "Cache-Control": "private, max-age=60",
        },
      })
    }

    const buf = fs.readFileSync(filePath)
    return new NextResponse(buf, {
      status: 200,
      headers: {
        "Content-Type": type,
        "Content-Length": String(buf.length),
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=60",
      },
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
