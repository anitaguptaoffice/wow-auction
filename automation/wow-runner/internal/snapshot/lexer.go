package snapshot

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"strconv"
	"strings"
)

type tokenKind uint8

const (
	tokenEOF tokenKind = iota
	tokenLBrace
	tokenRBrace
	tokenLBracket
	tokenRBracket
	tokenEqual
	tokenComma
	tokenSemicolon
	tokenString
	tokenNumber
	tokenIdentifier
)

type sourcePosition struct {
	offset int64
	line   int
	column int
}

type token struct {
	kind tokenKind
	text string
	pos  sourcePosition
}

type lexer struct {
	r      *bufio.Reader
	offset int64
	line   int
	column int
}

func newLexer(r *bufio.Reader) *lexer {
	l := &lexer{r: r, line: 1, column: 1}
	if prefix, err := r.Peek(3); err == nil && bytes.Equal(prefix, []byte{0xef, 0xbb, 0xbf}) {
		_, _ = r.Discard(3)
		l.offset = 3
		l.column = 4
	}
	return l
}

func (l *lexer) position() sourcePosition {
	return sourcePosition{offset: l.offset, line: l.line, column: l.column}
}

func (l *lexer) readByte() (byte, error) {
	b, err := l.r.ReadByte()
	if err != nil {
		return 0, err
	}
	l.offset++
	if b == '\n' {
		l.line++
		l.column = 1
	} else {
		l.column++
	}
	return b, nil
}

func (l *lexer) peekByte() (byte, error) {
	b, err := l.r.Peek(1)
	if err != nil {
		return 0, err
	}
	return b[0], nil
}

func (l *lexer) syntax(pos sourcePosition, format string, args ...any) error {
	return fmt.Errorf("line %d, column %d (byte %d): %s", pos.line, pos.column, pos.offset, fmt.Sprintf(format, args...))
}

func isLuaSpace(b byte) bool {
	switch b {
	case ' ', '\t', '\n', '\r', '\f', '\v':
		return true
	default:
		return false
	}
}

// longOpenLength reports the byte length and equals-sign count of a Lua long
// bracket opener currently at the front of the reader: [[ or [=[, etc.
func (l *lexer) longOpenLength() (length, equals int, ok bool, err error) {
	for index := 0; ; index++ {
		prefix, peekErr := l.r.Peek(index + 1)
		if peekErr != nil {
			if peekErr == io.EOF {
				return 0, 0, false, nil
			}
			return 0, 0, false, peekErr
		}
		b := prefix[index]
		if index == 0 {
			if b != '[' {
				return 0, 0, false, nil
			}
			continue
		}
		if b == '=' {
			equals++
			continue
		}
		if b == '[' {
			return index + 1, equals, true, nil
		}
		return 0, 0, false, nil
	}
}

func (l *lexer) consume(count int) error {
	for i := 0; i < count; i++ {
		if _, err := l.readByte(); err != nil {
			return err
		}
	}
	return nil
}

func (l *lexer) skipSpaceAndComments() error {
	for {
		b, err := l.peekByte()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if isLuaSpace(b) {
			_, _ = l.readByte()
			continue
		}
		prefix, _ := l.r.Peek(2)
		if len(prefix) < 2 || prefix[0] != '-' || prefix[1] != '-' {
			return nil
		}
		if err := l.consume(2); err != nil {
			return err
		}
		length, equals, long, err := l.longOpenLength()
		if err != nil {
			return err
		}
		if long {
			if err := l.consume(length); err != nil {
				return err
			}
			if _, err := l.scanLongString(equals, false, l.position()); err != nil {
				return err
			}
			continue
		}
		for {
			commentByte, readErr := l.readByte()
			if readErr == io.EOF {
				return nil
			}
			if readErr != nil {
				return readErr
			}
			if commentByte == '\n' {
				break
			}
		}
	}
}

func (l *lexer) next() (token, error) {
	if err := l.skipSpaceAndComments(); err != nil {
		return token{}, err
	}
	pos := l.position()
	b, err := l.peekByte()
	if err == io.EOF {
		return token{kind: tokenEOF, pos: pos}, nil
	}
	if err != nil {
		return token{}, err
	}

	switch b {
	case '{':
		_, _ = l.readByte()
		return token{kind: tokenLBrace, pos: pos}, nil
	case '}':
		_, _ = l.readByte()
		return token{kind: tokenRBrace, pos: pos}, nil
	case ']':
		_, _ = l.readByte()
		return token{kind: tokenRBracket, pos: pos}, nil
	case '=':
		_, _ = l.readByte()
		return token{kind: tokenEqual, pos: pos}, nil
	case ',':
		_, _ = l.readByte()
		return token{kind: tokenComma, pos: pos}, nil
	case ';':
		_, _ = l.readByte()
		return token{kind: tokenSemicolon, pos: pos}, nil
	case '[':
		length, equals, long, longErr := l.longOpenLength()
		if longErr != nil {
			return token{}, longErr
		}
		if long {
			if err := l.consume(length); err != nil {
				return token{}, err
			}
			value, err := l.scanLongString(equals, true, pos)
			if err != nil {
				return token{}, err
			}
			return token{kind: tokenString, text: value, pos: pos}, nil
		}
		_, _ = l.readByte()
		return token{kind: tokenLBracket, pos: pos}, nil
	case '\'', '"':
		value, err := l.scanQuotedString(b, pos)
		if err != nil {
			return token{}, err
		}
		return token{kind: tokenString, text: value, pos: pos}, nil
	}

	if isIdentifierStart(b) {
		value, err := l.scanWhile(isIdentifierContinue)
		if err != nil {
			return token{}, err
		}
		return token{kind: tokenIdentifier, text: value, pos: pos}, nil
	}
	if isNumberStart(l.r) {
		value, err := l.scanNumber(pos)
		if err != nil {
			return token{}, err
		}
		return token{kind: tokenNumber, text: value, pos: pos}, nil
	}
	return token{}, l.syntax(pos, "unexpected byte 0x%02x", b)
}

func isIdentifierStart(b byte) bool {
	return b == '_' || b >= 'A' && b <= 'Z' || b >= 'a' && b <= 'z'
}

func isIdentifierContinue(b byte) bool {
	return isIdentifierStart(b) || b >= '0' && b <= '9'
}

func (l *lexer) scanWhile(keep func(byte) bool) (string, error) {
	var out strings.Builder
	for {
		b, err := l.peekByte()
		if err == io.EOF {
			return out.String(), nil
		}
		if err != nil {
			return "", err
		}
		if !keep(b) {
			return out.String(), nil
		}
		_, _ = l.readByte()
		out.WriteByte(b)
	}
}

func isNumberStart(r *bufio.Reader) bool {
	prefix, err := r.Peek(2)
	if len(prefix) == 0 {
		return false
	}
	b := prefix[0]
	if b >= '0' && b <= '9' {
		return true
	}
	if b == '.' || b == '-' {
		if err != nil || len(prefix) < 2 {
			return false
		}
		return prefix[1] >= '0' && prefix[1] <= '9' || prefix[1] == '.'
	}
	return false
}

func isNumberDelimiter(b byte) bool {
	return isLuaSpace(b) || strings.ContainsRune("{}[],;=", rune(b))
}

func (l *lexer) scanNumber(pos sourcePosition) (string, error) {
	value, err := l.scanWhile(func(b byte) bool { return !isNumberDelimiter(b) })
	if err != nil {
		return "", err
	}
	if _, err := strconv.ParseFloat(value, 64); err != nil {
		return "", l.syntax(pos, "invalid Lua number %q", value)
	}
	return value, nil
}

func (l *lexer) scanQuotedString(quote byte, pos sourcePosition) (string, error) {
	_, _ = l.readByte() // opening quote
	var out strings.Builder
	for {
		b, err := l.readByte()
		if err == io.EOF {
			return "", l.syntax(pos, "unterminated quoted string")
		}
		if err != nil {
			return "", err
		}
		if b == quote {
			return out.String(), nil
		}
		if b == '\n' || b == '\r' {
			return "", l.syntax(pos, "unescaped newline in quoted string")
		}
		if b != '\\' {
			out.WriteByte(b)
			continue
		}

		escapePos := l.position()
		escaped, err := l.readByte()
		if err == io.EOF {
			return "", l.syntax(pos, "unterminated string escape")
		}
		if err != nil {
			return "", err
		}
		switch escaped {
		case 'a':
			out.WriteByte('\a')
		case 'b':
			out.WriteByte('\b')
		case 'f':
			out.WriteByte('\f')
		case 'n':
			out.WriteByte('\n')
		case 'r':
			out.WriteByte('\r')
		case 't':
			out.WriteByte('\t')
		case 'v':
			out.WriteByte('\v')
		case '\\', '\'', '"':
			out.WriteByte(escaped)
		case '\n':
			out.WriteByte('\n')
		case '\r':
			if next, peekErr := l.peekByte(); peekErr == nil && next == '\n' {
				_, _ = l.readByte()
			}
			out.WriteByte('\n')
		case 'z':
			for {
				next, peekErr := l.peekByte()
				if peekErr == io.EOF {
					break
				}
				if peekErr != nil {
					return "", peekErr
				}
				if !isLuaSpace(next) {
					break
				}
				_, _ = l.readByte()
			}
		case 'x':
			value, err := l.readHexEscape(escapePos)
			if err != nil {
				return "", err
			}
			out.WriteByte(value)
		default:
			if escaped < '0' || escaped > '9' {
				return "", l.syntax(escapePos, "invalid string escape \\%c", escaped)
			}
			value, err := l.readDecimalEscape(escaped, escapePos)
			if err != nil {
				return "", err
			}
			out.WriteByte(value)
		}
	}
}

func (l *lexer) readHexEscape(pos sourcePosition) (byte, error) {
	var raw [2]byte
	for i := range raw {
		b, err := l.readByte()
		if err != nil {
			return 0, l.syntax(pos, "incomplete hexadecimal string escape")
		}
		raw[i] = b
	}
	value, err := strconv.ParseUint(string(raw[:]), 16, 8)
	if err != nil {
		return 0, l.syntax(pos, "invalid hexadecimal string escape")
	}
	return byte(value), nil
}

func (l *lexer) readDecimalEscape(first byte, pos sourcePosition) (byte, error) {
	raw := []byte{first}
	for len(raw) < 3 {
		b, err := l.peekByte()
		if err == io.EOF {
			break
		}
		if err != nil {
			return 0, err
		}
		if b < '0' || b > '9' {
			break
		}
		_, _ = l.readByte()
		raw = append(raw, b)
	}
	value, err := strconv.ParseUint(string(raw), 10, 8)
	if err != nil {
		return 0, l.syntax(pos, "decimal string escape exceeds 255")
	}
	return byte(value), nil
}

func (l *lexer) scanLongString(equals int, capture bool, pos sourcePosition) (string, error) {
	var out strings.Builder
	first := true
	for {
		b, err := l.readByte()
		if err == io.EOF {
			return "", l.syntax(pos, "unterminated long string or comment")
		}
		if err != nil {
			return "", err
		}
		if first {
			first = false
			if b == '\n' {
				continue
			}
			if b == '\r' {
				if next, peekErr := l.peekByte(); peekErr == nil && next == '\n' {
					_, _ = l.readByte()
				}
				continue
			}
		}
		if b != ']' {
			if capture {
				out.WriteByte(b)
			}
			continue
		}

		matched := true
		consumedEquals := 0
		for consumedEquals < equals {
			next, peekErr := l.peekByte()
			if peekErr != nil || next != '=' {
				matched = false
				break
			}
			_, _ = l.readByte()
			consumedEquals++
		}
		if matched {
			next, peekErr := l.peekByte()
			if peekErr == nil && next == ']' {
				_, _ = l.readByte()
				return out.String(), nil
			}
		}
		if capture {
			out.WriteByte(']')
			for i := 0; i < consumedEquals; i++ {
				out.WriteByte('=')
			}
		}
	}
}
