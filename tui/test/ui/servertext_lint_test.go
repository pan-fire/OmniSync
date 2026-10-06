package ui_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/constant"
	"go/importer"
	"go/parser"
	"go/token"
	"go/types"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

const (
	uiPkg         = "github.com/pan-fire/OmniSync/tui/internal/ui"
	apiPkg        = "github.com/pan-fire/OmniSync/tui/internal/api"
	componentsPkg = "github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// Functions that make server text safe to draw, or draw it safely
// themselves: the table (components.Row), the flash message and the
// confirmation dialog sanitise what they are given.
var safeSinks = map[string]bool{
	uiPkg + ".safeLine":                      true,
	uiPkg + ".safeText":                      true,
	uiPkg + ".flash":                         true,
	uiPkg + ".errorFlash":                    true,
	componentsPkg + ".SafeLine":              true,
	componentsPkg + ".SafeText":              true,
	componentsPkg + ".NewConfirm":            true,
	componentsPkg + ".NewConfirmWord":        true,
	componentsPkg + ".NewFlash":              true,
	uiPkg + ".FlashMsg":                      true, // the app shows it through components.Flash
	componentsPkg + ".Row":                   true, // composite literal
	"(*" + componentsPkg + ".Table).SetRows": true,
}

// Functions that put their arguments into drawn text unchanged.
var drawSinks = map[string]bool{
	"fmt.Sprintf":                           true,
	"fmt.Sprint":                            true,
	"fmt.Sprintln":                          true,
	"fmt.Fprintf":                           true,
	"fmt.Fprint":                            true,
	"fmt.Fprintln":                          true,
	"(*strings.Builder).WriteString":        true,
	"(charm.land/lipgloss/v2.Style).Render": true,
	"charm.land/lipgloss/v2.JoinHorizontal": true,
	"charm.land/lipgloss/v2.JoinVertical":   true,
	"charm.land/bubbletea/v2.NewView":       true,
	uiPkg + ".mutedText":                    true,
	uiPkg + ".headerText":                   true,
}

// Functions whose result still holds their argument's text.
var passThrough = map[string]bool{
	"strings.ToUpper":           true,
	"strings.ToLower":           true,
	"strings.TrimSpace":         true,
	"strings.TrimRight":         true,
	"strings.TrimLeft":          true,
	"strings.TrimPrefix":        true,
	"strings.TrimSuffix":        true,
	"strings.Join":              true,
	"path.Base":                 true,
	"path/filepath.Base":        true,
	componentsPkg + ".Truncate": true,
}

// A view must not draw a string field of an API answer, or an error's
// text, as it is: it can
// hold a carriage return, a backspace or an escape sequence that draws
// over or restyles the view (see components.SafeLine). This finds every
// string field of an internal/api type (and every err.Error()) that
// reaches fmt, a strings.Builder
// or a lipgloss style in internal/ui without passing safeLine or
// safeText (or a component that sanitises: a table row, a flash message,
// a confirmation). The fuzz targets check the drawn result; this check
// fails on the code, before a view with server text in it exists.
func TestViews_DrawServerTextOnlyThroughSafeLine(t *testing.T) {
	if testing.Short() {
		t.Skip("type-checks internal/ui")
	}
	fset, files, info := typeCheckUI(t)
	var findings []string
	for _, file := range files {
		var stack []ast.Node
		ast.Inspect(file, func(n ast.Node) bool {
			if n == nil {
				stack = stack[:len(stack)-1]
				return true
			}
			stack = append(stack, n)
			expr, ok := n.(ast.Expr)
			if !ok || !isServerText(info, expr) {
				return true
			}
			if reachesDrawSink(info, stack) {
				findings = append(findings, fmt.Sprintf("%s: %s drawn without safeLine/safeText",
					relPos(fset, expr.Pos()), types.ExprString(expr)))
			}
			return true
		})
	}
	sort.Strings(findings)
	if len(findings) > 0 {
		t.Errorf("server text drawn as it is (%d):\n%s", len(findings), strings.Join(findings, "\n"))
	}
}

// isServerText reports whether expr is a string field of an API answer or
// an error's text (which can hold the backend's detail message).
func isServerText(info *types.Info, expr ast.Expr) bool {
	switch e := expr.(type) {
	case *ast.SelectorExpr:
		return isAPIStringField(info, e)
	case *ast.CallExpr:
		return calleeName(info, e) == "(error).Error"
	}
	return false
}

// isAPIStringField reports whether sel selects a field with string, a
// named string type or a pointer to one, of a struct in internal/api.
func isAPIStringField(info *types.Info, sel *ast.SelectorExpr) bool {
	s, ok := info.Selections[sel]
	if !ok || s.Kind() != types.FieldVal {
		return false
	}
	field := s.Obj()
	if field.Pkg() == nil || field.Pkg().Path() != apiPkg {
		return false
	}
	typ := field.Type()
	if p, isPtr := typ.(*types.Pointer); isPtr {
		typ = p.Elem()
	}
	b, ok := typ.Underlying().(*types.Basic)
	return ok && b.Kind() == types.String
}

// reachesDrawSink climbs from the selector (last in stack) through the
// expressions that keep its text to a call that draws it. A sanitiser on
// the way, or above the drawing call, makes it safe; any other call ends
// the search (its result is that function's text, not the field's).
func reachesDrawSink(info *types.Info, stack []ast.Node) bool {
	drawn := false
	for i := len(stack) - 2; i >= 0; i-- {
		switch n := stack[i].(type) {
		case *ast.ParenExpr, *ast.StarExpr, *ast.KeyValueExpr:
		case *ast.BinaryExpr:
			if n.Op != token.ADD {
				return drawn
			}
		case *ast.CompositeLit:
			if name := typeName(info.TypeOf(n)); safeSinks[name] {
				return false
			}
			if !drawn {
				return false
			}
		case *ast.CallExpr:
			if tv, ok := info.Types[n.Fun]; ok && tv.IsType() {
				continue // a conversion such as string(state)
			}
			name := calleeName(info, n)
			switch {
			case safeSinks[name]:
				return false
			case quotedArg(info, n, name, stack[i+1]):
				// %q escapes every control character.
				return false
			case drawSinks[name]:
				drawn = true
			case passThrough[name]:
			default:
				return drawn
			}
		default:
			return drawn
		}
	}
	return drawn
}

// quotedArg reports whether arg is formatted with %q by the fmt call.
func quotedArg(info *types.Info, call *ast.CallExpr, name string, arg ast.Node) bool {
	formatAt := map[string]int{"fmt.Sprintf": 0, "fmt.Fprintf": 1, "fmt.Errorf": 0}
	at, ok := formatAt[name]
	if !ok || len(call.Args) <= at {
		return false
	}
	tv := info.Types[call.Args[at]]
	if tv.Value == nil || tv.Value.Kind() != constant.String {
		return false
	}
	want := -1
	for i, a := range call.Args[at+1:] {
		if a == arg {
			want = i
		}
	}
	if want < 0 {
		return false
	}
	format := constant.StringVal(tv.Value)
	argNum := 0
	for i := 0; i < len(format); i++ {
		if format[i] != '%' {
			continue
		}
		i++
		for i < len(format) && strings.IndexByte("+-# 0123456789.*[]", format[i]) >= 0 {
			switch format[i] {
			case '[':
				return false // explicit argument indexes: not worth following
			case '*':
				argNum++
			}
			i++
		}
		if i >= len(format) || format[i] == '%' {
			continue
		}
		if argNum == want {
			return format[i] == 'q'
		}
		argNum++
	}
	return false
}

func typeName(t types.Type) string {
	if t == nil {
		return ""
	}
	if n, ok := t.(*types.Named); ok && n.Obj().Pkg() != nil {
		return n.Obj().Pkg().Path() + "." + n.Obj().Name()
	}
	return t.String()
}

// calleeName names the called function as types.Func.FullName does.
func calleeName(info *types.Info, call *ast.CallExpr) string {
	var id *ast.Ident
	switch f := ast.Unparen(call.Fun).(type) {
	case *ast.Ident:
		id = f
	case *ast.SelectorExpr:
		id = f.Sel
	case *ast.IndexExpr: // a generic function
		if s, ok := f.X.(*ast.SelectorExpr); ok {
			id = s.Sel
		} else if i, ok := f.X.(*ast.Ident); ok {
			id = i
		}
	}
	if id == nil {
		return ""
	}
	if fn, ok := info.Uses[id].(*types.Func); ok {
		return fn.Origin().FullName()
	}
	return ""
}

func relPos(fset *token.FileSet, p token.Pos) string {
	pos := fset.Position(p)
	return fmt.Sprintf("%s:%d", filepath.Base(pos.Filename), pos.Line)
}

// typeCheckUI type-checks internal/ui from its source, with the export
// data of its dependencies from `go list -export`.
func typeCheckUI(t *testing.T) (*token.FileSet, []*ast.File, *types.Info) {
	t.Helper()
	// go test puts its own go command first on PATH.
	cmd := exec.Command("go", "list", "-export", "-deps", "-json=ImportPath,Export,Dir,GoFiles", uiPkg)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("go list: %v\n%s", err, stderr.String())
	}
	exports := map[string]string{}
	var dir string
	var goFiles []string
	dec := json.NewDecoder(bytes.NewReader(out))
	for {
		var p struct {
			ImportPath, Export, Dir string
			GoFiles                 []string
		}
		if err := dec.Decode(&p); errors.Is(err, io.EOF) {
			break
		} else if err != nil {
			t.Fatal(err)
		}
		exports[p.ImportPath] = p.Export
		if p.ImportPath == uiPkg {
			dir, goFiles = p.Dir, p.GoFiles
		}
	}
	fset := token.NewFileSet()
	var files []*ast.File
	for _, name := range goFiles {
		f, err := parser.ParseFile(fset, filepath.Join(dir, name), nil, parser.SkipObjectResolution)
		if err != nil {
			t.Fatal(err)
		}
		files = append(files, f)
	}
	conf := types.Config{Importer: importer.ForCompiler(fset, "gc", func(path string) (io.ReadCloser, error) {
		file, ok := exports[path]
		if !ok || file == "" {
			return nil, fmt.Errorf("no export data for %s", path)
		}
		return os.Open(file)
	})}
	info := &types.Info{
		Types:      map[ast.Expr]types.TypeAndValue{},
		Uses:       map[*ast.Ident]types.Object{},
		Selections: map[*ast.SelectorExpr]*types.Selection{},
	}
	if _, err := conf.Check(uiPkg, fset, files, info); err != nil {
		t.Fatal(err)
	}
	return fset, files, info
}
