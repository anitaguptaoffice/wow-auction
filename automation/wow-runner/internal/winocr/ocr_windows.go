//go:build windows

package winocr

import (
	"context"
	"errors"
	"fmt"
	"image"
	"image/color"
	"math"
	"runtime"
	"sync"
	"syscall"
	"time"
	"unsafe"

	ole "github.com/go-ole/go-ole"
	"golang.org/x/sys/windows"
)

const (
	defaultLanguage = "zh-Hans-CN"

	roInitMultithreaded = 1

	asyncStarted   = 0
	asyncCompleted = 1
	asyncCanceled  = 2
	asyncError     = 3

	bitmapPixelFormatBGRA8 = 87
	bitmapAlphaModeIgnore  = 2
)

var (
	iidLanguageFactory       = ole.NewGUID("{9B0252AC-0C27-44F8-B792-9793FB66C63E}")
	iidOcrEngineStatics      = ole.NewGUID("{5BFFA85A-3384-3540-9940-699120D428A8}")
	iidBufferFactory         = ole.NewGUID("{71AF914D-C10F-484B-BC50-14BC623B3A27}")
	iidBufferByteAccess      = ole.NewGUID("{905A0FEF-BC53-11DF-8C49-001E4FC686DA}")
	iidSoftwareBitmapStatics = ole.NewGUID("{DF0385DB-672F-4A9D-806E-C2442F343E86}")
	iidAsyncInfo             = ole.NewGUID("{00000036-0000-0000-C000-000000000046}")

	combase        = windows.NewLazySystemDLL("combase.dll")
	roUninitialize = combase.NewProc("RoUninitialize")
)

type request struct {
	ctx    context.Context
	pixels []byte
	width  int
	height int
	result chan response
}

type response struct {
	result Result
	err    error
}

// Engine owns a dedicated, locked MTA thread. WinRT apartment setup is
// per-thread, while ordinary Go goroutines are free to migrate between OS
// threads, so all COM calls are serialized through that worker.
type Engine struct {
	language string
	requests chan request
	done     chan struct{}

	mu     sync.RWMutex
	closed bool
}

// New creates a Windows.Media.Ocr engine for language. An empty language uses
// zh-Hans-CN. The exact installed language is checked; there is no silent
// fallback to a different recognizer.
func New(language string) (*Engine, error) {
	if language == "" {
		language = defaultLanguage
	}
	e := &Engine{
		language: language,
		requests: make(chan request),
		done:     make(chan struct{}),
	}
	initialized := make(chan error, 1)
	go e.run(initialized)
	if err := <-initialized; err != nil {
		<-e.done
		return nil, err
	}
	return e, nil
}

// Recognize crops roi, converts it to tightly packed BGRA8, and sends those
// pixels directly to Windows.Media.Ocr. An empty roi means the whole image.
func (e *Engine) Recognize(ctx context.Context, src image.Image, roi image.Rectangle) (Result, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	pixels, width, height, err := packBGRA(src, roi)
	if err != nil {
		return Result{}, err
	}

	e.mu.RLock()
	defer e.mu.RUnlock()
	if e.closed {
		return Result{}, ErrClosed
	}
	resp := make(chan response, 1)
	req := request{ctx: ctx, pixels: pixels, width: width, height: height, result: resp}
	select {
	case e.requests <- req:
	case <-ctx.Done():
		return Result{}, ctx.Err()
	}
	select {
	case out := <-resp:
		return out.result, out.err
	case <-ctx.Done():
		// The worker observes the same context and cancels the WinRT operation.
		// Waiting for its buffered reply is unnecessary for the caller.
		return Result{}, ctx.Err()
	}
}

// Close waits for in-flight recognition and releases the WinRT engine on its
// owning thread. It is safe to call more than once.
func (e *Engine) Close() error {
	e.mu.Lock()
	if e.closed {
		e.mu.Unlock()
		return nil
	}
	e.closed = true
	close(e.requests)
	e.mu.Unlock()
	<-e.done
	return nil
}

func (e *Engine) run(initialized chan<- error) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	defer close(e.done)

	if err := initializeWinRT(); err != nil {
		initialized <- err
		return
	}
	defer roUninitialize.Call()

	engine, err := createOcrEngine(e.language)
	if err != nil {
		initialized <- err
		return
	}
	defer release(engine)
	initialized <- nil

	for req := range e.requests {
		if err := req.ctx.Err(); err != nil {
			req.result <- response{err: err}
			continue
		}
		result, err := recognizePixels(req.ctx, engine, e.language, req.pixels, req.width, req.height)
		req.result <- response{result: result, err: err}
	}
}

func initializeWinRT() error {
	err := ole.RoInitialize(roInitMultithreaded)
	if err == nil {
		return nil
	}
	var oleErr *ole.OleError
	if errors.As(err, &oleErr) && int32(oleErr.Code()) >= 0 {
		return nil
	}
	return fmt.Errorf("winocr: RoInitialize: %w", err)
}

func createOcrEngine(language string) (unsafe.Pointer, error) {
	languageFactory, err := activationFactory("Windows.Globalization.Language", iidLanguageFactory)
	if err != nil {
		return nil, err
	}
	defer release(languageFactory)

	hLanguage, err := ole.NewHString(language)
	if err != nil {
		return nil, fmt.Errorf("winocr: language HSTRING: %w", err)
	}
	defer ole.DeleteHString(hLanguage)
	var languageObject unsafe.Pointer
	if err := callHRESULT(languageFactory, 6, uintptr(hLanguage), uintptr(unsafe.Pointer(&languageObject))); err != nil {
		return nil, fmt.Errorf("winocr: create language %q: %w", language, err)
	}
	if languageObject == nil {
		return nil, fmt.Errorf("winocr: create language %q returned nil", language)
	}
	defer release(languageObject)

	statics, err := activationFactory("Windows.Media.Ocr.OcrEngine", iidOcrEngineStatics)
	if err != nil {
		return nil, err
	}
	defer release(statics)

	var supported byte
	if err := callHRESULT(statics, 8, uintptr(languageObject), uintptr(unsafe.Pointer(&supported))); err != nil {
		return nil, fmt.Errorf("winocr: check language %q: %w", language, err)
	}
	if supported == 0 {
		return nil, fmt.Errorf("winocr: OCR language %q is not installed", language)
	}

	var engine unsafe.Pointer
	if err := callHRESULT(statics, 9, uintptr(languageObject), uintptr(unsafe.Pointer(&engine))); err != nil {
		return nil, fmt.Errorf("winocr: create OCR engine for %q: %w", language, err)
	}
	if engine == nil {
		return nil, fmt.Errorf("winocr: OCR engine for %q returned nil", language)
	}
	return engine, nil
}

func recognizePixels(ctx context.Context, engine unsafe.Pointer, language string, pixels []byte, width, height int) (Result, error) {
	buffer, err := newBuffer(pixels)
	if err != nil {
		return Result{}, err
	}
	defer release(buffer)

	bitmapStatics, err := activationFactory("Windows.Graphics.Imaging.SoftwareBitmap", iidSoftwareBitmapStatics)
	if err != nil {
		return Result{}, err
	}
	defer release(bitmapStatics)

	var bitmap unsafe.Pointer
	if err := callHRESULT(
		bitmapStatics,
		10,
		uintptr(buffer),
		bitmapPixelFormatBGRA8,
		uintptr(width),
		uintptr(height),
		bitmapAlphaModeIgnore,
		uintptr(unsafe.Pointer(&bitmap)),
	); err != nil {
		return Result{}, fmt.Errorf("winocr: create SoftwareBitmap: %w", err)
	}
	if bitmap == nil {
		return Result{}, errors.New("winocr: create SoftwareBitmap returned nil")
	}
	defer release(bitmap)

	var operation unsafe.Pointer
	if err := callHRESULT(engine, 6, uintptr(bitmap), uintptr(unsafe.Pointer(&operation))); err != nil {
		return Result{}, fmt.Errorf("winocr: RecognizeAsync: %w", err)
	}
	if operation == nil {
		return Result{}, errors.New("winocr: RecognizeAsync returned nil")
	}
	defer release(operation)

	ocrResult, err := awaitOcrResult(ctx, operation)
	if err != nil {
		return Result{}, err
	}
	defer release(ocrResult)

	text, err := getHString(ocrResult, 8)
	if err != nil {
		return Result{}, fmt.Errorf("winocr: read result text: %w", err)
	}
	words, err := collectWords(ocrResult)
	if err != nil {
		return Result{}, err
	}
	return Result{Text: text, Words: words, Language: language}, nil
}

func newBuffer(pixels []byte) (unsafe.Pointer, error) {
	if len(pixels) == 0 || uint64(len(pixels)) > math.MaxUint32 {
		return nil, fmt.Errorf("winocr: invalid pixel buffer length %d", len(pixels))
	}
	factory, err := activationFactory("Windows.Storage.Streams.Buffer", iidBufferFactory)
	if err != nil {
		return nil, err
	}
	defer release(factory)

	var buffer unsafe.Pointer
	if err := callHRESULT(factory, 6, uintptr(len(pixels)), uintptr(unsafe.Pointer(&buffer))); err != nil {
		return nil, fmt.Errorf("winocr: create IBuffer: %w", err)
	}
	if buffer == nil {
		return nil, errors.New("winocr: create IBuffer returned nil")
	}

	access, err := queryInterface(buffer, iidBufferByteAccess)
	if err != nil {
		release(buffer)
		return nil, fmt.Errorf("winocr: query IBufferByteAccess: %w", err)
	}
	var destination unsafe.Pointer
	if err := callHRESULT(access, 3, uintptr(unsafe.Pointer(&destination))); err != nil {
		release(access)
		release(buffer)
		return nil, fmt.Errorf("winocr: access IBuffer bytes: %w", err)
	}
	if destination == nil {
		release(access)
		release(buffer)
		return nil, errors.New("winocr: IBuffer byte pointer is nil")
	}
	copy(unsafe.Slice((*byte)(destination), len(pixels)), pixels)
	release(access)
	if err := callHRESULT(buffer, 8, uintptr(len(pixels))); err != nil {
		release(buffer)
		return nil, fmt.Errorf("winocr: set IBuffer length: %w", err)
	}
	return buffer, nil
}

func awaitOcrResult(ctx context.Context, operation unsafe.Pointer) (unsafe.Pointer, error) {
	info, err := queryInterface(operation, iidAsyncInfo)
	if err != nil {
		return nil, fmt.Errorf("winocr: query IAsyncInfo: %w", err)
	}
	defer release(info)
	defer callNoResult(info, 10) // IAsyncInfo.Close

	ticker := time.NewTicker(8 * time.Millisecond)
	defer ticker.Stop()
	for {
		var status int32
		if err := callHRESULT(info, 7, uintptr(unsafe.Pointer(&status))); err != nil {
			return nil, fmt.Errorf("winocr: read async status: %w", err)
		}
		switch status {
		case asyncCompleted:
			var result unsafe.Pointer
			if err := callHRESULT(operation, 8, uintptr(unsafe.Pointer(&result))); err != nil {
				return nil, fmt.Errorf("winocr: get OCR result: %w", err)
			}
			if result == nil {
				return nil, errors.New("winocr: completed OCR returned nil")
			}
			return result, nil
		case asyncCanceled:
			return nil, errors.New("winocr: OCR operation was canceled")
		case asyncError:
			var code int32
			if err := callHRESULT(info, 8, uintptr(unsafe.Pointer(&code))); err != nil {
				return nil, fmt.Errorf("winocr: OCR operation failed (and ErrorCode failed: %v)", err)
			}
			return nil, hresultError(uintptr(uint32(code)))
		case asyncStarted:
			// Continue below.
		default:
			return nil, fmt.Errorf("winocr: unexpected async status %d", status)
		}

		select {
		case <-ctx.Done():
			callNoResult(info, 9) // IAsyncInfo.Cancel
			return nil, ctx.Err()
		case <-ticker.C:
		}
	}
}

func collectWords(result unsafe.Pointer) ([]Word, error) {
	var lines unsafe.Pointer
	if err := callHRESULT(result, 6, uintptr(unsafe.Pointer(&lines))); err != nil {
		return nil, fmt.Errorf("winocr: get OCR lines: %w", err)
	}
	if lines == nil {
		return nil, nil
	}
	defer release(lines)

	lineCount, err := vectorSize(lines)
	if err != nil {
		return nil, fmt.Errorf("winocr: get OCR line count: %w", err)
	}
	wordsOut := make([]Word, 0, lineCount)
	for i := uint32(0); i < lineCount; i++ {
		line, err := vectorGet(lines, i)
		if err != nil {
			return nil, fmt.Errorf("winocr: get OCR line %d: %w", i, err)
		}
		if line == nil {
			continue
		}
		var words unsafe.Pointer
		err = callHRESULT(line, 6, uintptr(unsafe.Pointer(&words)))
		release(line)
		if err != nil {
			return nil, fmt.Errorf("winocr: get words for line %d: %w", i, err)
		}
		if words == nil {
			continue
		}
		wordCount, err := vectorSize(words)
		if err != nil {
			release(words)
			return nil, fmt.Errorf("winocr: get word count for line %d: %w", i, err)
		}
		for j := uint32(0); j < wordCount; j++ {
			word, err := vectorGet(words, j)
			if err != nil {
				release(words)
				return nil, fmt.Errorf("winocr: get word %d/%d: %w", i, j, err)
			}
			if word == nil {
				continue
			}
			text, textErr := getHString(word, 7)
			var rect winRTRect
			rectErr := callHRESULT(word, 6, uintptr(unsafe.Pointer(&rect)))
			release(word)
			if textErr != nil {
				release(words)
				return nil, fmt.Errorf("winocr: get word text: %w", textErr)
			}
			if rectErr != nil {
				release(words)
				return nil, fmt.Errorf("winocr: get word bounds: %w", rectErr)
			}
			wordsOut = append(wordsOut, Word{Text: text, Bounds: rect.imageRect()})
		}
		release(words)
	}
	return wordsOut, nil
}

type winRTRect struct {
	X      float32
	Y      float32
	Width  float32
	Height float32
}

func (r winRTRect) imageRect() image.Rectangle {
	return image.Rect(
		int(math.Floor(float64(r.X))),
		int(math.Floor(float64(r.Y))),
		int(math.Ceil(float64(r.X+r.Width))),
		int(math.Ceil(float64(r.Y+r.Height))),
	)
}

func vectorSize(vector unsafe.Pointer) (uint32, error) {
	var size uint32
	err := callHRESULT(vector, 7, uintptr(unsafe.Pointer(&size)))
	return size, err
}

func vectorGet(vector unsafe.Pointer, index uint32) (unsafe.Pointer, error) {
	var value unsafe.Pointer
	err := callHRESULT(vector, 6, uintptr(index), uintptr(unsafe.Pointer(&value)))
	return value, err
}

func getHString(object unsafe.Pointer, slot int) (string, error) {
	var value ole.HString
	if err := callHRESULT(object, slot, uintptr(unsafe.Pointer(&value))); err != nil {
		return "", err
	}
	if value == 0 {
		return "", nil
	}
	text := value.String()
	if err := ole.DeleteHString(value); err != nil {
		return "", err
	}
	return text, nil
}

func activationFactory(class string, iid *ole.GUID) (unsafe.Pointer, error) {
	factory, err := ole.RoGetActivationFactory(class, iid)
	if err != nil {
		return nil, fmt.Errorf("winocr: activate %s: %w", class, err)
	}
	return unsafe.Pointer(factory), nil
}

func queryInterface(object unsafe.Pointer, iid *ole.GUID) (unsafe.Pointer, error) {
	if object == nil {
		return nil, errors.New("winocr: QueryInterface on nil")
	}
	var result unsafe.Pointer
	if err := callHRESULT(object, 0, uintptr(unsafe.Pointer(iid)), uintptr(unsafe.Pointer(&result))); err != nil {
		return nil, err
	}
	return result, nil
}

func callHRESULT(object unsafe.Pointer, slot int, args ...uintptr) error {
	if object == nil {
		return errors.New("winocr: COM call on nil")
	}
	callArgs := make([]uintptr, 1, len(args)+1)
	callArgs[0] = uintptr(object)
	callArgs = append(callArgs, args...)
	hr, _, _ := syscall.SyscallN(vtableMethod(object, slot), callArgs...)
	if int32(hr) < 0 {
		return hresultError(hr)
	}
	return nil
}

func callNoResult(object unsafe.Pointer, slot int) {
	if object != nil {
		syscall.SyscallN(vtableMethod(object, slot), uintptr(object))
	}
}

func release(object unsafe.Pointer) {
	if object != nil {
		syscall.SyscallN(vtableMethod(object, 2), uintptr(object))
	}
}

func vtableMethod(object unsafe.Pointer, slot int) uintptr {
	vtable := *(*unsafe.Pointer)(object)
	return *(*uintptr)(unsafe.Add(vtable, uintptr(slot)*unsafe.Sizeof(uintptr(0))))
}

func hresultError(hr uintptr) error {
	return fmt.Errorf("winocr: HRESULT 0x%08X", uint32(hr))
}

func packBGRA(src image.Image, roi image.Rectangle) ([]byte, int, int, error) {
	if src == nil {
		return nil, 0, 0, errors.New("winocr: nil image")
	}
	bounds := src.Bounds()
	if roi.Empty() {
		roi = bounds
	} else {
		roi = roi.Intersect(bounds)
	}
	if roi.Empty() {
		return nil, 0, 0, fmt.Errorf("winocr: empty ROI after clipping to %v", bounds)
	}
	width, height := roi.Dx(), roi.Dy()
	if width > math.MaxInt32 || height > math.MaxInt32 || uint64(width)*uint64(height)*4 > math.MaxUint32 {
		return nil, 0, 0, fmt.Errorf("winocr: ROI %dx%d is too large", width, height)
	}
	pixels := make([]byte, width*height*4)
	offset := 0
	if rgba, ok := src.(*image.RGBA); ok {
		for y := roi.Min.Y; y < roi.Max.Y; y++ {
			row := rgba.PixOffset(roi.Min.X, y)
			for x := 0; x < width; x++ {
				at := row + x*4
				pixels[offset+0] = rgba.Pix[at+2]
				pixels[offset+1] = rgba.Pix[at+1]
				pixels[offset+2] = rgba.Pix[at+0]
				pixels[offset+3] = 0xff
				offset += 4
			}
		}
		return pixels, width, height, nil
	}
	for y := roi.Min.Y; y < roi.Max.Y; y++ {
		for x := roi.Min.X; x < roi.Max.X; x++ {
			c := color.RGBAModel.Convert(src.At(x, y)).(color.RGBA)
			pixels[offset+0] = c.B
			pixels[offset+1] = c.G
			pixels[offset+2] = c.R
			pixels[offset+3] = 0xff
			offset += 4
		}
	}
	return pixels, width, height, nil
}
