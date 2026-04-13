package vision

import (
	"image"
	"image/color"
	"math"
)

const grayEps = 1e-9

func rgbaToGray01(c color.RGBA) float64 {
	return (0.299*float64(c.R) + 0.587*float64(c.G) + 0.114*float64(c.B)) / 255.0
}

// nccScoreAt：灰度 NCC（与 OpenCV TM_CCOEFF_NORMED 一致），输出映射到 [0,1]。
func nccScoreAt(screen *image.RGBA, tmpl *image.RGBA, x, y int) float64 {
	tb := tmpl.Bounds()
	tw, th := tb.Dx(), tb.Dy()
	n := float64(tw * th)
	var sumS, sumT float64
	for j := 0; j < th; j++ {
		for i := 0; i < tw; i++ {
			sumS += rgbaToGray01(screen.RGBAAt(x+i, y+j))
			sumT += rgbaToGray01(tmpl.RGBAAt(tb.Min.X+i, tb.Min.Y+j))
		}
	}
	meanS := sumS / n
	meanT := sumT / n
	var num, denS, denT float64
	for j := 0; j < th; j++ {
		for i := 0; i < tw; i++ {
			ds := rgbaToGray01(screen.RGBAAt(x+i, y+j)) - meanS
			dt := rgbaToGray01(tmpl.RGBAAt(tb.Min.X+i, tb.Min.Y+j)) - meanT
			num += ds * dt
			denS += ds * ds
			denT += dt * dt
		}
	}
	// 完全平坦且模板与块一致时 denS、denT 为 0，相关系数按 1 处理（与「图案相同」一致）。
	if denS < 1e-14 && denT < 1e-14 {
		return 1
	}
	den := math.Sqrt(denS*denT) + grayEps
	r := num / den
	if r < -1 {
		r = -1
	}
	if r > 1 {
		r = 1
	}
	return (r + 1) / 2
}

func meanAbsRGBChannelAvg(screen *image.RGBA, tmpl *image.RGBA, x, y int) float64 {
	tb := tmpl.Bounds()
	tw, th := tb.Dx(), tb.Dy()
	var sum float64
	n := 0
	for j := 0; j < th; j++ {
		for i := 0; i < tw; i++ {
			a := screen.RGBAAt(x+i, y+j)
			b := tmpl.RGBAAt(tb.Min.X+i, tb.Min.Y+j)
			sum += math.Abs(float64(a.R)-float64(b.R)) +
				math.Abs(float64(a.G)-float64(b.G)) +
				math.Abs(float64(a.B)-float64(b.B))
			n += 3
		}
	}
	if n == 0 {
		return 255
	}
	return sum / float64(n)
}
