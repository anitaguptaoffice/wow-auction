package vision

import (
	"fmt"
	"image"
	"image/draw"
	"image/png"
	"math"
	"os"
)

// BestMatch 在 ROI 内滑窗；opts 为 nil 时使用 NCC（与 OpenCV TM_CCOEFF_NORMED 映射到 [0,1]）。
func BestMatch(screen *image.RGBA, tmpl *image.RGBA, roi image.Rectangle, opts *MatchOptions) (score float64, at image.Point, ok bool) {
	if opts == nil {
		opts = DefaultMatchOptions()
	}
	sb := screen.Bounds()
	tb := tmpl.Bounds()
	tw, th := tb.Dx(), tb.Dy()
	if tw <= 0 || th <= 0 {
		return 0, image.Point{}, false
	}
	if roi.Dx() == 0 || roi.Dy() == 0 {
		roi = sb
	} else {
		roi = roi.Intersect(sb)
	}
	if roi.Empty() || tw > roi.Dx() || th > roi.Dy() {
		return 0, image.Point{}, false
	}
	best := -1.0
	bestAt := image.Point{}
	for y := roi.Min.Y; y <= roi.Max.Y-th; y++ {
		for x := roi.Min.X; x <= roi.Max.X-tw; x++ {
			var s float64
			switch opts.Method {
			case MatchMethodRGBMean:
				s = similarityAt(screen, tmpl, x, y)
			case MatchMethodNCC:
				s = nccScoreAt(screen, tmpl, x, y)
			default:
				s = nccScoreAt(screen, tmpl, x, y)
			}
			if opts.ColorGateMaxAvgChannelDiff > 0 {
				d := meanAbsRGBChannelAvg(screen, tmpl, x, y)
				if d > opts.ColorGateMaxAvgChannelDiff {
					continue
				}
			}
			if s > best {
				best = s
				bestAt = image.Point{X: x, Y: y}
			}
		}
	}
	return best, bestAt, best >= 0
}

// BestSimilarity 保留用于测试与兼容：RGB 通道平均相似度（旧算法）。
func BestSimilarity(screen *image.RGBA, tmpl *image.RGBA, roi image.Rectangle) (score float64, at image.Point, ok bool) {
	return BestMatch(screen, tmpl, roi, &MatchOptions{Method: MatchMethodRGBMean})
}

func similarityAt(screen, tmpl *image.RGBA, x, y int) float64 {
	tb := tmpl.Bounds()
	var sum float64
	n := 0
	for j := 0; j < tb.Dy(); j++ {
		for i := 0; i < tb.Dx(); i++ {
			c1 := screen.RGBAAt(x+i, y+j)
			c2 := tmpl.RGBAAt(tb.Min.X+i, tb.Min.Y+j)
			for _, pair := range []struct{ a, b uint32 }{
				{uint32(c1.R), uint32(c2.R)},
				{uint32(c1.G), uint32(c2.G)},
				{uint32(c1.B), uint32(c2.B)},
			} {
				d := math.Abs(float64(pair.a) - float64(pair.b))
				sum += 1 - d/255.0
				n++
			}
		}
	}
	if n == 0 {
		return 0
	}
	return sum / float64(n)
}

// LoadPNG loads a PNG path into RGBA.
func LoadPNG(path string) (*image.RGBA, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	img, err := png.Decode(f)
	if err != nil {
		return nil, fmt.Errorf("decode png: %w", err)
	}
	b := img.Bounds()
	out := image.NewRGBA(b)
	draw.Draw(out, b, img, b.Min, draw.Src)
	return out, nil
}
