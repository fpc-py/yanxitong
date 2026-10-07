// CSV 下载工具：Blob/字符串统一走这里，字符串自动补 UTF-8 BOM（Excel 打开中文不乱码）

async function hasBom(blob: Blob): Promise<boolean> {
  if (blob.size < 3) return false
  const head = new Uint8Array(await blob.slice(0, 3).arrayBuffer())
  return head[0] === 0xef && head[1] === 0xbb && head[2] === 0xbf
}

/** 触发浏览器下载 CSV；后端已带 BOM 时不再重复添加（重复 BOM 会显示为乱码字符） */
export async function downloadCsv(content: Blob | string, filename: string): Promise<void> {
  let blob: Blob
  if (typeof content === 'string') {
    blob = new Blob([`\uFEFF${content}`], { type: 'text/csv;charset=utf-8' })
  } else if (await hasBom(content)) {
    blob = content
  } else {
    blob = new Blob([new Uint8Array([0xef, 0xbb, 0xbf]), content], { type: 'text/csv;charset=utf-8' })
  }
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}
