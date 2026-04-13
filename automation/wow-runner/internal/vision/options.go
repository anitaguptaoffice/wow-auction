package vision

// MatchMethod 与 OpenCV 常见用法对应：NCC≈TM_CCOEFF_NORMED；RGBMean 为早期简易实现。
type MatchMethod int

const (
	MatchMethodRGBMean MatchMethod = iota
	MatchMethodNCC
)

// MatchOptions 控制单次匹配；nil 等价于默认 NCC、无颜色门控。
type MatchOptions struct {
	Method                     MatchMethod
	ColorGateMaxAvgChannelDiff float64 // 0 表示关闭；>0 时在最佳位置上校验 RGB 平均绝对差（每通道 0–255 空间再对三通道取平均）
}

// DefaultMatchOptions 使用 NCC，与 OpenCV matchTemplate(TM_CCOEFF_NORMED) 数值区间一致映射到 [0,1]。
func DefaultMatchOptions() *MatchOptions {
	return &MatchOptions{Method: MatchMethodNCC, ColorGateMaxAvgChannelDiff: 0}
}
