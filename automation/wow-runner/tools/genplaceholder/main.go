// 生成 64x64 灰色占位 PNG，供模板路径占位；后续用实机截图覆盖同一路径或改配置。
package main

import (
	"flag"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"os"
)

func main() {
	// 默认路径与 config.DefaultPlaceholderTemplate 一致，便于复制进配置。
	out := flag.String("out", "assets/placeholder.png", "output path")
	flag.Parse()
	if err := os.MkdirAll(dirOf(*out), 0755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	img := image.NewRGBA(image.Rect(0, 0, 64, 64))
	g := color.RGBA{R: 128, G: 128, B: 160, A: 255}
	for y := 0; y < 64; y++ {
		for x := 0; x < 64; x++ {
			img.SetRGBA(x, y, g)
		}
	}
	f, err := os.Create(*out)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer f.Close()
	if err := png.Encode(f, img); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println("wrote", *out)
}

func dirOf(p string) string {
	for i := len(p) - 1; i >= 0; i-- {
		if p[i] == '/' || p[i] == '\\' {
			return p[:i]
		}
	}
	return "."
}
