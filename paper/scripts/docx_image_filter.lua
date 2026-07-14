-- Use the 300 dpi PNG previews in Word; keep vector PDFs in the LaTeX build.
function Image(image)
  image.src = image.src:gsub("%.pdf$", ".png")
  return image
end
