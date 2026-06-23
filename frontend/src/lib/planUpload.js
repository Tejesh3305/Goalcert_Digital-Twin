/**
 * planUpload.js — turn a dropped/selected plan file into something uploadable.
 *
 * One helper used by both BIM Studio and the Build-a-Twin drop zone:
 *   - PNG/JPG/WEBP  → read straight to a data-URL (the Plan Parser sees an image)
 *   - PDF           → render page 1 to a PNG data-URL with pdfjs-dist
 *   - IFC/DXF       → read to a data-URL, tagged `bim` for the IFC fast-follow
 *
 * Returns { dataUrl, filename, kind } where kind ∈ 'image' | 'pdf' | 'bim'.
 * The backend re-derives `type` from the filename extension (see twin_upload),
 * so the kind here is only for local preview decisions.
 */

const IMAGE_EXT = ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'svg']
const BIM_EXT = ['ifc', 'dxf']

export const ACCEPT = '.png,.jpg,.jpeg,.webp,.gif,.bmp,.svg,.pdf,.ifc,.dxf,image/*,application/pdf'

export function extOf(name = '') {
  const m = String(name).toLowerCase().match(/\.([a-z0-9]+)$/)
  return m ? m[1] : ''
}

export function kindOf(name = '') {
  const ext = extOf(name)
  if (ext === 'pdf') return 'pdf'
  if (BIM_EXT.includes(ext)) return 'bim'
  if (IMAGE_EXT.includes(ext)) return 'image'
  return 'image' // optimistic default; backend re-checks
}

function readAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result)
    r.onerror = () => reject(r.error || new Error('Could not read file'))
    r.readAsDataURL(file)
  })
}

let _pdfjs = null
async function getPdfjs() {
  if (_pdfjs) return _pdfjs
  // Lazy-load so the heavy PDF engine isn't pulled into the main bundle.
  const pdfjs = await import('pdfjs-dist')
  const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url')
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default
  _pdfjs = pdfjs
  return pdfjs
}

async function pdfPageOneToPng(file, maxDim = 2000) {
  const pdfjs = await getPdfjs()
  const buf = await file.arrayBuffer()
  const pdf = await pdfjs.getDocument({ data: buf }).promise
  const page = await pdf.getPage(1)
  const base = page.getViewport({ scale: 1 })
  const scale = Math.min(maxDim / base.width, maxDim / base.height, 3)
  const viewport = page.getViewport({ scale: Math.max(scale, 1) })
  const canvas = document.createElement('canvas')
  canvas.width = Math.ceil(viewport.width)
  canvas.height = Math.ceil(viewport.height)
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  await page.render({ canvasContext: ctx, viewport }).promise
  return canvas.toDataURL('image/png')
}

/**
 * Read a plan file into a previewable/uploadable data-URL.
 * @param {File} file
 * @returns {Promise<{dataUrl: string, filename: string, kind: string}>}
 */
export async function readPlanFile(file) {
  const filename = file.name || 'plan'
  const kind = kindOf(filename)
  if (kind === 'pdf') {
    const dataUrl = await pdfPageOneToPng(file)
    // hand the parser an image; keep the .png name so the backend tags it `image`
    return { dataUrl, filename: filename.replace(/\.pdf$/i, '.png'), kind: 'pdf' }
  }
  const dataUrl = await readAsDataURL(file)
  return { dataUrl, filename, kind }
}
