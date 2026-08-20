package snapshot

import (
	"bufio"
	"fmt"
	"strconv"
)

type tableRole uint8

const (
	roleGeneric tableRole = iota
	roleRoot
	roleLastScan
	roleAuctions
	roleDay
	roleScans
	roleScan
	roleItems
	roleItem
)

type metadataBuilder struct {
	value Metadata
	seen  uint8
}

const (
	seenItemCount uint8 = 1 << iota
	seenRecordCount
	seenMissingCoreCount
	seenAPIErrorCount
	seenLinkedItemCount
	seenIncompleteInfoCount
	seenAllMetadata = seenItemCount | seenRecordCount | seenMissingCoreCount |
		seenAPIErrorCount | seenLinkedItemCount | seenIncompleteInfoCount
)

type scanBuilder struct {
	metadata      metadataBuilder
	timestamp     int64
	timestampSeen bool
	itemsSeen     bool
	actualItems   int64
}

type parser struct {
	lexer *lexer
	look  token
	has   bool

	report           Report
	lastMetadata     metadataBuilder
	lastScanSeen     bool
	lastScanTimeSeen bool
	auctionsSeen     bool
}

func newParser(r *bufio.Reader) *parser {
	return &parser{lexer: newLexer(r)}
}

func (p *parser) parse() (Report, error) {
	name, err := p.next()
	if err != nil {
		return Report{}, err
	}
	if name.kind != tokenIdentifier || name.text != "AuctionSearchDB" {
		return Report{}, p.unexpected(name, "AuctionSearchDB assignment")
	}
	if err := p.expect(tokenEqual, "'=' after AuctionSearchDB"); err != nil {
		return Report{}, err
	}
	if err := p.expect(tokenLBrace, "root table"); err != nil {
		return Report{}, err
	}
	if err := p.parseTableBody(roleRoot, nil); err != nil {
		return Report{}, err
	}

	tail, err := p.peek()
	if err != nil {
		return Report{}, err
	}
	if tail.kind == tokenSemicolon {
		_, _ = p.next()
		tail, err = p.peek()
		if err != nil {
			return Report{}, err
		}
	}
	if tail.kind != tokenEOF {
		return Report{}, p.unexpected(tail, "end of file")
	}
	if err := p.finishReport(); err != nil {
		return Report{}, err
	}
	return p.report, nil
}

func (p *parser) next() (token, error) {
	if p.has {
		p.has = false
		return p.look, nil
	}
	return p.lexer.next()
}

func (p *parser) peek() (token, error) {
	if p.has {
		return p.look, nil
	}
	tok, err := p.lexer.next()
	if err != nil {
		return token{}, err
	}
	p.look = tok
	p.has = true
	return tok, nil
}

func (p *parser) expect(kind tokenKind, description string) error {
	tok, err := p.next()
	if err != nil {
		return err
	}
	if tok.kind != kind {
		return p.unexpected(tok, description)
	}
	return nil
}

func (p *parser) unexpected(tok token, expected string) error {
	return p.lexer.syntax(tok.pos, "expected %s, found %s", expected, describeToken(tok))
}

func describeToken(tok token) string {
	switch tok.kind {
	case tokenEOF:
		return "end of file"
	case tokenLBrace:
		return "'{'"
	case tokenRBrace:
		return "'}'"
	case tokenLBracket:
		return "'['"
	case tokenRBracket:
		return "']'"
	case tokenEqual:
		return "'='"
	case tokenComma:
		return "','"
	case tokenSemicolon:
		return "';'"
	case tokenString:
		return "string"
	case tokenNumber:
		return "number " + strconv.Quote(tok.text)
	case tokenIdentifier:
		return "identifier " + strconv.Quote(tok.text)
	default:
		return "unknown token"
	}
}

// parseTableBody parses after an already-consumed '{' and consumes its
// matching '}'. The grammar enforcement here is what makes a truncated file
// fail even when all desired metadata appeared near its beginning.
func (p *parser) parseTableBody(role tableRole, currentScan *scanBuilder) error {
	for {
		tok, err := p.peek()
		if err != nil {
			return err
		}
		if tok.kind == tokenRBrace {
			_, _ = p.next()
			return nil
		}
		if tok.kind == tokenEOF {
			return p.unexpected(tok, "table field or '}'")
		}
		if err := p.parseField(role, currentScan); err != nil {
			return err
		}

		separator, err := p.peek()
		if err != nil {
			return err
		}
		switch separator.kind {
		case tokenComma, tokenSemicolon:
			_, _ = p.next()
		case tokenRBrace:
			// A final field need not have a trailing separator.
		default:
			return p.unexpected(separator, "',' ';' or '}' after table field")
		}
	}
}

func (p *parser) parseField(role tableRole, currentScan *scanBuilder) error {
	first, err := p.next()
	if err != nil {
		return err
	}
	key := ""
	keyed := false

	switch first.kind {
	case tokenLBracket:
		keyToken, err := p.next()
		if err != nil {
			return err
		}
		switch keyToken.kind {
		case tokenString, tokenNumber, tokenIdentifier:
			key = keyToken.text
		default:
			return p.unexpected(keyToken, "scalar table key")
		}
		if err := p.expect(tokenRBracket, "']' after table key"); err != nil {
			return err
		}
		if err := p.expect(tokenEqual, "'=' after table key"); err != nil {
			return err
		}
		keyed = true
		first, err = p.next()
		if err != nil {
			return err
		}
	case tokenIdentifier:
		next, err := p.peek()
		if err != nil {
			return err
		}
		if next.kind == tokenEqual {
			_, _ = p.next()
			key = first.text
			keyed = true
			first, err = p.next()
			if err != nil {
				return err
			}
		}
	}

	return p.parseFieldValue(role, currentScan, key, keyed, first)
}

func (p *parser) parseFieldValue(
	role tableRole,
	currentScan *scanBuilder,
	key string,
	keyed bool,
	first token,
) error {
	if requiresNumericField(role, key, keyed) && first.kind != tokenNumber {
		return p.unexpected(first, "integer value for "+key)
	}
	if requiresTableField(role, key, keyed) && first.kind != tokenLBrace {
		return p.unexpected(first, "table value for "+key)
	}
	if (role == roleAuctions || role == roleScans || role == roleItems) && first.kind != tokenLBrace {
		return p.unexpected(first, "nested table record")
	}

	if first.kind != tokenLBrace {
		if err := validateScalar(first); err != nil {
			return err
		}
		return p.recordScalar(role, currentScan, key, keyed, first)
	}

	child := childRole(role, key, keyed)
	switch child {
	case roleLastScan:
		if p.lastScanSeen {
			return p.lexer.syntax(first.pos, "duplicate lastScan table")
		}
		p.lastScanSeen = true
		return p.parseTableBody(roleLastScan, nil)
	case roleAuctions:
		if p.auctionsSeen {
			return p.lexer.syntax(first.pos, "duplicate auctions table")
		}
		p.auctionsSeen = true
		return p.parseTableBody(roleAuctions, nil)
	case roleScan:
		builder := &scanBuilder{}
		if err := p.parseTableBody(roleScan, builder); err != nil {
			return err
		}
		summary, err := finishScan(builder, len(p.report.Scans))
		if err != nil {
			return p.lexer.syntax(first.pos, "%v", err)
		}
		p.report.Scans = append(p.report.Scans, summary)
		return nil
	case roleItems:
		if currentScan == nil {
			return p.lexer.syntax(first.pos, "items table outside a scan")
		}
		if currentScan.itemsSeen {
			return p.lexer.syntax(first.pos, "duplicate items table in scan")
		}
		currentScan.itemsSeen = true
		return p.parseTableBody(roleItems, currentScan)
	case roleItem:
		if currentScan == nil {
			return p.lexer.syntax(first.pos, "item record outside a scan")
		}
		if err := p.parseTableBody(roleItem, currentScan); err != nil {
			return err
		}
		currentScan.actualItems++
		return nil
	default:
		return p.parseTableBody(child, currentScan)
	}
}

func childRole(parent tableRole, key string, keyed bool) tableRole {
	switch parent {
	case roleRoot:
		if keyed && key == "lastScan" {
			return roleLastScan
		}
		if keyed && key == "auctions" {
			return roleAuctions
		}
	case roleAuctions:
		return roleDay
	case roleDay:
		if keyed && key == "scans" {
			return roleScans
		}
	case roleScans:
		return roleScan
	case roleScan:
		if keyed && key == "items" {
			return roleItems
		}
	case roleItems:
		return roleItem
	}
	return roleGeneric
}

func requiresTableField(role tableRole, key string, keyed bool) bool {
	if !keyed {
		return false
	}
	return role == roleRoot && (key == "lastScan" || key == "auctions") ||
		role == roleDay && key == "scans" ||
		role == roleScan && key == "items"
}

func requiresNumericField(role tableRole, key string, keyed bool) bool {
	if !keyed {
		return false
	}
	if role == roleRoot && key == "lastScanTime" || role == roleScan && key == "timestamp" {
		return true
	}
	if role != roleLastScan && role != roleScan {
		return false
	}
	return metadataFieldBit(key) != 0
}

func validateScalar(tok token) error {
	switch tok.kind {
	case tokenString, tokenNumber:
		return nil
	case tokenIdentifier:
		if tok.text == "true" || tok.text == "false" || tok.text == "nil" {
			return nil
		}
	}
	return fmt.Errorf("line %d, column %d: invalid scalar %s", tok.pos.line, tok.pos.column, describeToken(tok))
}

func (p *parser) recordScalar(
	role tableRole,
	currentScan *scanBuilder,
	key string,
	keyed bool,
	tok token,
) error {
	if !keyed {
		return nil
	}
	if role == roleRoot && key == "lastScanTime" {
		if p.lastScanTimeSeen {
			return p.lexer.syntax(tok.pos, "duplicate lastScanTime")
		}
		value, err := integerToken(tok, "lastScanTime")
		if err != nil {
			return err
		}
		p.lastScanTimeSeen = true
		p.report.LastScanTime = value
		return nil
	}
	if role == roleLastScan {
		return p.lastMetadata.set(key, tok)
	}
	if role != roleScan || currentScan == nil {
		return nil
	}
	if key == "timestamp" {
		if currentScan.timestampSeen {
			return p.lexer.syntax(tok.pos, "duplicate scan timestamp")
		}
		value, err := integerToken(tok, "scan timestamp")
		if err != nil {
			return err
		}
		currentScan.timestamp = value
		currentScan.timestampSeen = true
		return nil
	}
	return currentScan.metadata.set(key, tok)
}

func integerToken(tok token, label string) (int64, error) {
	if tok.kind != tokenNumber {
		return 0, fmt.Errorf("%s must be an integer", label)
	}
	value, err := strconv.ParseInt(tok.text, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("line %d, column %d: %s must be an integer, got %q", tok.pos.line, tok.pos.column, label, tok.text)
	}
	return value, nil
}

func metadataFieldBit(key string) uint8 {
	switch key {
	case "itemCount":
		return seenItemCount
	case "recordCount":
		return seenRecordCount
	case "missingCoreCount":
		return seenMissingCoreCount
	case "apiErrorCount":
		return seenAPIErrorCount
	case "linkedItemCount":
		return seenLinkedItemCount
	case "incompleteInfoCount":
		return seenIncompleteInfoCount
	default:
		return 0
	}
}

func (m *metadataBuilder) set(key string, tok token) error {
	bit := metadataFieldBit(key)
	if bit == 0 {
		return nil
	}
	if m.seen&bit != 0 {
		return fmt.Errorf("line %d, column %d: duplicate metadata field %s", tok.pos.line, tok.pos.column, key)
	}
	value, err := integerToken(tok, key)
	if err != nil {
		return err
	}
	if value < 0 {
		return fmt.Errorf("line %d, column %d: %s cannot be negative", tok.pos.line, tok.pos.column, key)
	}
	m.seen |= bit
	switch bit {
	case seenItemCount:
		m.value.ItemCount = value
	case seenRecordCount:
		m.value.RecordCount = value
	case seenMissingCoreCount:
		m.value.MissingCoreCount = value
	case seenAPIErrorCount:
		m.value.APIErrorCount = value
	case seenLinkedItemCount:
		m.value.LinkedItemCount = value
	case seenIncompleteInfoCount:
		m.value.IncompleteInfoCount = value
	}
	return nil
}

func (m metadataBuilder) validate(label string) error {
	if m.seen != seenAllMetadata {
		return fmt.Errorf("%s is missing metadata fields (mask 0x%02x, want 0x%02x)", label, m.seen, seenAllMetadata)
	}
	counters := []struct {
		name  string
		value int64
	}{
		{name: "missingCoreCount", value: m.value.MissingCoreCount},
		{name: "apiErrorCount", value: m.value.APIErrorCount},
		{name: "linkedItemCount", value: m.value.LinkedItemCount},
		{name: "incompleteInfoCount", value: m.value.IncompleteInfoCount},
	}
	for _, counter := range counters {
		if counter.value > m.value.RecordCount {
			return fmt.Errorf(
				"%s.%s=%d exceeds recordCount=%d",
				label,
				counter.name,
				counter.value,
				m.value.RecordCount,
			)
		}
	}
	return nil
}

func finishScan(scan *scanBuilder, index int) (ScanSummary, error) {
	label := fmt.Sprintf("scan[%d]", index)
	if !scan.timestampSeen || scan.timestamp <= 0 {
		return ScanSummary{}, fmt.Errorf("%s has no positive timestamp", label)
	}
	if !scan.itemsSeen {
		return ScanSummary{}, fmt.Errorf("%s has no items table", label)
	}
	if err := scan.metadata.validate(label); err != nil {
		return ScanSummary{}, err
	}
	metadata := scan.metadata.value
	if metadata.RecordCount != scan.actualItems {
		return ScanSummary{}, fmt.Errorf(
			"%s recordCount=%d but items contains %d records",
			label,
			metadata.RecordCount,
			scan.actualItems,
		)
	}
	if metadata.ItemCount != scan.actualItems {
		return ScanSummary{}, fmt.Errorf(
			"%s itemCount=%d but items contains %d records",
			label,
			metadata.ItemCount,
			scan.actualItems,
		)
	}
	return ScanSummary{
		Timestamp:       scan.timestamp,
		Metadata:        metadata,
		ActualItemCount: scan.actualItems,
	}, nil
}

func (p *parser) finishReport() error {
	if !p.lastScanSeen {
		return fmt.Errorf("missing lastScan table")
	}
	if !p.lastScanTimeSeen || p.report.LastScanTime <= 0 {
		return fmt.Errorf("missing positive lastScanTime")
	}
	if !p.auctionsSeen {
		return fmt.Errorf("missing auctions table")
	}
	if err := p.lastMetadata.validate("lastScan"); err != nil {
		return err
	}
	if len(p.report.Scans) == 0 {
		return fmt.Errorf("auctions contains no scans")
	}

	latest := p.report.Scans[0]
	for _, scan := range p.report.Scans[1:] {
		// On equal second-resolution timestamps, the later serialized scan is
		// the most recently inserted one.
		if scan.Timestamp >= latest.Timestamp {
			latest = scan
		}
	}
	if latest.Timestamp != p.report.LastScanTime {
		return fmt.Errorf(
			"latest scan timestamp=%d does not match lastScanTime=%d",
			latest.Timestamp,
			p.report.LastScanTime,
		)
	}
	if latest.Metadata != p.lastMetadata.value {
		return fmt.Errorf("latest scan metadata does not match lastScan metadata")
	}
	p.report.LastScan = p.lastMetadata.value
	p.report.Latest = latest
	return nil
}
