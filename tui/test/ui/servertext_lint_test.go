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
// text, as it is: it can hold a carriage return, a backspace or an escape
// sequence that draws over or restyles the view (see components.SafeLine).
// This finds server text that reaches fmt, a strings.Builder or a lipgloss
// style in internal/ui without passing safeLine or safeText (or a
// component that sanitises: a table row, a flash message, a confirmation).
// Server text is a string field (or []string) of an internal/api type,
// err.Error(), and whatever internal/ui stores it in, followed until
// nothing new turns up:
//   - a local variable or a struct field (m.authURL = resp.AuthURL, a
//     composite literal such as loadedMsg{name: p.Name}, x += ...);
//   - a range variable over such a slice, and append to one;
//   - a parameter of an internal/ui function called with it;
//   - the result of an internal/ui function that returns it.
//
// It follows text through string concatenation, conversions and the
// passThrough functions; a sanitiser on the way makes it safe. It does not
// follow maps, channels, interface values (any, tea.Msg asserted back),
// closures' results, pointers to a string taken with &, or text that
// leaves internal/ui and comes back; a field is tainted for every value of
// its struct type. The fuzz targets check the drawn result; this check
// fails on the code, before a view with server text in it exists.
func TestViews_DrawServerTextOnlyThroughSafeLine(t *testing.T) {
	if testing.Short() {
		t.Skip("type-checks internal/ui")
	}
	fset, files, info := typeCheckUI(t, nil)
	if findings := serverTextFindings(fset, files, info); len(findings) > 0 {
		t.Errorf("server text drawn as it is (%d):\n%s", len(findings), strings.Join(findings, "\n"))
	}
}

// The check itself: each way server text can be stored before a view
// draws it is found, and a sanitiser anywhere on the way clears it. The
// source is type-checked as internal/ui, against the real internal/api.
func TestViews_ServerTextLintFollowsStoredText(t *testing.T) {
	if testing.Short() {
		t.Skip("type-checks internal/ui")
	}
	const src = `package ui

import (
	"fmt"
	"strings"

	"github.com/pan-fire/OmniSync/tui/internal/api"
)

func safeLine(s string) string { return s }

type model struct {
	authURL, safeURL string
	names            []string
	msg              note
}

type note struct{ text string }

func (m *model) store(resp *api.AuthorizeResponse, h *api.HealthResponse) {
	m.authURL = resp.AuthURL
	m.safeURL = safeLine(resp.AuthURL)
	m.names = append(m.names, h.Status)
	m.msg = note{text: "status " + h.Status}
}

func label(s string) string { return "[" + s + "]" }

func drawParam(b *strings.Builder, param string) { b.WriteString(param) } // finding: parameter

func status(h *api.HealthResponse) string { return strings.TrimSpace(h.Status) }

func (m model) view(h *api.HealthResponse) string {
	var b strings.Builder
	b.WriteString("  " + m.authURL)            // finding: field
	b.WriteString(m.safeURL)                   // sanitised when stored
	local := m.authURL
	b.WriteString(local)                       // finding: local variable
	for _, n := range m.names {
		b.WriteString(n)                       // finding: range variable
	}
	b.WriteString(strings.Join(m.names, ","))  // finding: slice
	b.WriteString(m.msg.text)                  // finding: composite literal
	b.WriteString(label(h.Status))             // finding: result via parameter
	b.WriteString(status(h))                   // finding: result
	drawParam(&b, h.Status)
	b.WriteString(safeLine(status(h)))         // sanitised
	b.WriteString(fmt.Sprintf("%q", m.authURL)) // quoted
	return b.String()
}
`
	fset, files, info := typeCheckUI(t, map[string]string{"lint_example.go": src})
	got := strings.Join(serverTextFindings(fset, files, info), "\n")
	for _, want := range []string{
		": m.authURL drawn", ": local drawn", ": n drawn", ": m.names drawn", ": m.msg.text drawn",
		": param drawn", ": label(h.Status) drawn", ": status(h) drawn",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("no finding %q in:\n%s", want, got)
		}
	}
	if n := strings.Count(got, "\n") + 1; got == "" || n != 8 {
		t.Errorf("%d findings, want 8:\n%s", n, got)
	}
}

// serverTextFindings lists where server text in files is drawn as it is.
func serverTextFindings(fset *token.FileSet, files []*ast.File, info *types.Info) []string {
	tainted := taintedObjects(info, files)
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
			if !ok || !isServerText(info, tainted, expr) {
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
	return findings
}

// isServerText reports whether expr is server text in its own right: a
// string field of an API answer, an error's text, or a variable, field or
// call result that taintedObjects found to hold one. (Parts of a larger
// expression that carries the text are reported where they are used, by
// reachesDrawSink.)
func isServerText(info *types.Info, tainted map[types.Object]bool, expr ast.Expr) bool {
	switch e := expr.(type) {
	case *ast.SelectorExpr:
		if isAPIStringField(info, e) {
			return true
		}
		s, ok := info.Selections[e]
		return ok && s.Kind() == types.FieldVal && tainted[s.Obj()]
	case *ast.Ident:
		v, ok := info.Uses[e].(*types.Var)
		return ok && !v.IsField() && tainted[v]
	case *ast.IndexExpr:
		return carriesServerText(info, tainted, e.X)
	case *ast.CallExpr:
		if calleeName(info, e) == "(error).Error" {
			return true
		}
		fn := calleeFunc(info, e)
		return fn != nil && tainted[fn]
	}
	return false
}

// carriesServerText reports whether the value of expr holds server text:
// it is server text, or keeps some through concatenation, a conversion, a
// slice or index, append or a passThrough function.
func carriesServerText(info *types.Info, tainted map[types.Object]bool, expr ast.Expr) bool {
	expr = ast.Unparen(expr)
	if isServerText(info, tainted, expr) {
		return true
	}
	switch e := expr.(type) {
	case *ast.BinaryExpr:
		return e.Op == token.ADD && (carriesServerText(info, tainted, e.X) || carriesServerText(info, tainted, e.Y))
	case *ast.SliceExpr:
		return carriesServerText(info, tainted, e.X)
	case *ast.StarExpr:
		return carriesServerText(info, tainted, e.X)
	case *ast.CallExpr:
		if tv, ok := info.Types[e.Fun]; ok && tv.IsType() {
			return len(e.Args) == 1 && carriesServerText(info, tainted, e.Args[0])
		}
		builtin := false
		if id, ok := ast.Unparen(e.Fun).(*ast.Ident); ok {
			b, isBuiltin := info.Uses[id].(*types.Builtin)
			builtin = isBuiltin && b.Name() == "append"
		}
		if !builtin && !passThrough[calleeName(info, e)] {
			return false
		}
		for _, a := range e.Args {
			if carriesServerText(info, tainted, a) {
				return true
			}
		}
	}
	return false
}

// taintedObjects finds the variables, struct fields and functions of
// internal/ui that hold or return server text (see the test's comment),
// repeating the search until it finds nothing new.
func taintedObjects(info *types.Info, files []*ast.File) map[types.Object]bool {
	tainted := map[types.Object]bool{}
	changed := true
	mark := func(obj types.Object) {
		if obj == nil || tainted[obj] || !holdsText(obj) {
			return
		}
		tainted[obj] = true
		changed = true
	}
	carries := func(e ast.Expr) bool { return carriesServerText(info, tainted, e) }
	for changed {
		changed = false
		for _, file := range files {
			for _, decl := range file.Decls {
				fd, ok := decl.(*ast.FuncDecl)
				if ok && fd.Body != nil {
					markReturns(info, fd, carries, mark)
				}
			}
			ast.Inspect(file, func(n ast.Node) bool {
				switch n := n.(type) {
				case *ast.AssignStmt:
					if len(n.Lhs) == len(n.Rhs) {
						for i, lhs := range n.Lhs {
							if carries(n.Rhs[i]) {
								mark(assignedObject(info, lhs))
							}
						}
					} else if len(n.Rhs) == 1 && carries(n.Rhs[0]) {
						for _, lhs := range n.Lhs {
							mark(assignedObject(info, lhs))
						}
					}
				case *ast.ValueSpec:
					for i, name := range n.Names {
						if i < len(n.Values) && carries(n.Values[i]) {
							mark(info.Defs[name])
						}
					}
				case *ast.RangeStmt:
					if n.Value != nil && carries(n.X) {
						mark(assignedObject(info, n.Value))
					}
				case *ast.CompositeLit:
					st, ok := typeUnder[*types.Struct](info.TypeOf(n))
					if !ok {
						return true
					}
					for i, elt := range n.Elts {
						if kv, isKV := elt.(*ast.KeyValueExpr); isKV {
							if key, isID := kv.Key.(*ast.Ident); isID && carries(kv.Value) {
								mark(info.Uses[key])
							}
						} else if i < st.NumFields() && carries(elt) {
							mark(st.Field(i))
						}
					}
				case *ast.CallExpr:
					// A sink's own parameters are not followed: the call
					// is where the text is drawn, or made safe.
					fn := calleeFunc(info, n)
					if fn == nil || fn.Pkg() == nil || fn.Pkg().Path() != uiPkg ||
						drawSinks[fn.FullName()] || safeSinks[fn.FullName()] {
						return true
					}
					params := fn.Signature().Params()
					for i, a := range n.Args {
						if params.Len() == 0 || !carries(a) {
							continue
						}
						mark(params.At(min(i, params.Len()-1)))
					}
				}
				return true
			})
		}
	}
	return tainted
}

// markReturns marks fd's function when one of its return statements (not
// those of closures inside it) carries server text.
func markReturns(info *types.Info, fd *ast.FuncDecl, carries func(ast.Expr) bool, mark func(types.Object)) {
	ast.Inspect(fd.Body, func(n ast.Node) bool {
		switch n := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.ReturnStmt:
			for _, r := range n.Results {
				if carries(r) {
					mark(info.Defs[fd.Name])
				}
			}
		}
		return true
	})
}

// assignedObject is the variable or struct field an assignment's left side
// names, or nil.
func assignedObject(info *types.Info, lhs ast.Expr) types.Object {
	switch e := ast.Unparen(lhs).(type) {
	case *ast.Ident:
		if obj := info.Defs[e]; obj != nil {
			return obj
		}
		return info.Uses[e]
	case *ast.SelectorExpr:
		if s, ok := info.Selections[e]; ok && s.Kind() == types.FieldVal {
			return s.Obj()
		}
	case *ast.IndexExpr:
		return assignedObject(info, e.X)
	}
	return nil
}

// holdsText reports whether obj is a variable or field of a string type
// (or a pointer to or slice of one), or a function returning one.
func holdsText(obj types.Object) bool {
	switch o := obj.(type) {
	case *types.Var:
		return isTextType(o.Type())
	case *types.Func:
		res := o.Signature().Results()
		for i := 0; i < res.Len(); i++ {
			if isTextType(res.At(i).Type()) {
				return true
			}
		}
	}
	return false
}

func isTextType(typ types.Type) bool {
	switch t := typ.Underlying().(type) {
	case *types.Pointer:
		return isTextType(t.Elem())
	case *types.Slice:
		return isTextType(t.Elem())
	case *types.Basic:
		return t.Kind() == types.String
	}
	return false
}

func typeUnder[T types.Type](typ types.Type) (T, bool) {
	var zero T
	if typ == nil {
		return zero, false
	}
	if p, ok := typ.Underlying().(*types.Pointer); ok {
		typ = p.Elem()
	}
	t, ok := typ.Underlying().(T)
	return t, ok
}

// calleeFunc is the function or method call calls, or nil.
func calleeFunc(info *types.Info, call *ast.CallExpr) *types.Func {
	var id *ast.Ident
	switch f := ast.Unparen(call.Fun).(type) {
	case *ast.Ident:
		id = f
	case *ast.SelectorExpr:
		id = f.Sel
	case *ast.IndexExpr:
		if s, ok := f.X.(*ast.SelectorExpr); ok {
			id = s.Sel
		} else if i, ok := f.X.(*ast.Ident); ok {
			id = i
		}
	}
	if id == nil {
		return nil
	}
	if fn, ok := info.Uses[id].(*types.Func); ok {
		return fn.Origin()
	}
	return nil
}

// isAPIStringField reports whether sel selects a field of a struct in
// internal/api whose type is a string, a named string type, or a pointer
// to or slice of one.
func isAPIStringField(info *types.Info, sel *ast.SelectorExpr) bool {
	s, ok := info.Selections[sel]
	if !ok || s.Kind() != types.FieldVal {
		return false
	}
	field := s.Obj()
	if field.Pkg() == nil || field.Pkg().Path() != apiPkg {
		return false
	}
	return isTextType(field.Type())
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
	if fn := calleeFunc(info, call); fn != nil {
		return fn.FullName()
	}
	return ""
}

func relPos(fset *token.FileSet, p token.Pos) string {
	pos := fset.Position(p)
	return fmt.Sprintf("%s:%d", filepath.Base(pos.Filename), pos.Line)
}

// typeCheckUI type-checks internal/ui from its source, with the export
// data of its dependencies from `go list -export`. With sources (file name
// to Go source), it type-checks those as internal/ui instead.
func typeCheckUI(t *testing.T, sources map[string]string) (*token.FileSet, []*ast.File, *types.Info) {
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
	if sources != nil {
		goFiles = goFiles[:0]
		for name := range sources {
			goFiles = append(goFiles, name)
		}
	}
	for _, name := range goFiles {
		var src any
		if sources != nil {
			src = sources[name]
		}
		f, err := parser.ParseFile(fset, filepath.Join(dir, name), src, parser.SkipObjectResolution)
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
		Defs:       map[*ast.Ident]types.Object{},
		Uses:       map[*ast.Ident]types.Object{},
		Selections: map[*ast.SelectorExpr]*types.Selection{},
	}
	if _, err := conf.Check(uiPkg, fset, files, info); err != nil {
		t.Fatal(err)
	}
	return fset, files, info
}
