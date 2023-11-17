from __future__ import annotations

import os
import sphinx

from breathe import parser

from sphinx import addnodes
from sphinx.domains import cpp, c, python
from sphinx.util.nodes import nested_parse_with_titles
from sphinx.util import url_re
from sphinx.ext.graphviz import graphviz

from docutils import nodes
from docutils.statemachine import StringList, UnexpectedIndentationError
from docutils.parsers.rst.states import Text

try:
    from sphinxcontrib import phpdomain as php  # type: ignore
except ImportError:
    php = None

try:
    from sphinx_csharp import csharp as cs  # type: ignore
except ImportError:
    cs = None

import re
import textwrap
from typing import Any, Callable, Optional, Type, TYPE_CHECKING, Union
from collections.abc import Iterable

if TYPE_CHECKING:
    from breathe.project import ProjectInfo
    from breathe.renderer import RenderContext
    from breathe.renderer.filter import Filter
    from breathe.renderer.target import TargetHandler

    from sphinx.application import Sphinx
    from sphinx.directives import ObjectDescription
    from docutils.nodes import Node, TextElement

ContentCallback = Callable[[addnodes.desc_content], None]
Declarator = Union[addnodes.desc_signature, addnodes.desc_signature_line]
DeclaratorCallback = Callable[[Declarator], None]

_debug_indent = 0


class WithContext:
    def __init__(self, parent: SphinxRenderer, context: RenderContext):
        self.context = context
        self.parent = parent
        self.previous = None

    def __enter__(self):
        assert self.previous is None
        self.previous = self.parent.context
        self.parent.set_context(self.context)
        return self

    def __exit__(self, et, ev, bt):
        self.parent.context = self.previous
        self.previous = None


class BaseObject:
    # Use this class as the first base class to make sure the overrides are used.
    # Set the content_callback attribute to a function taking a docutils node.

    def transform_content(self, contentnode: addnodes.desc_content) -> None:
        super().transform_content(contentnode)  # type: ignore
        callback = getattr(self, "breathe_content_callback", None)
        if callback is None:
            return
        callback(contentnode)


# ----------------------------------------------------------------------------


class CPPClassObject(BaseObject, cpp.CPPClassObject):
    pass


class CPPUnionObject(BaseObject, cpp.CPPUnionObject):
    pass


class CPPFunctionObject(BaseObject, cpp.CPPFunctionObject):
    pass


class CPPMemberObject(BaseObject, cpp.CPPMemberObject):
    pass


class CPPTypeObject(BaseObject, cpp.CPPTypeObject):
    pass


class CPPConceptObject(BaseObject, cpp.CPPConceptObject):
    pass


class CPPEnumObject(BaseObject, cpp.CPPEnumObject):
    pass


class CPPEnumeratorObject(BaseObject, cpp.CPPEnumeratorObject):
    pass


# ----------------------------------------------------------------------------


class CStructObject(BaseObject, c.CStructObject):
    pass


class CUnionObject(BaseObject, c.CUnionObject):
    pass


class CFunctionObject(BaseObject, c.CFunctionObject):
    pass


class CMemberObject(BaseObject, c.CMemberObject):
    pass


class CTypeObject(BaseObject, c.CTypeObject):
    pass


class CEnumObject(BaseObject, c.CEnumObject):
    pass


class CEnumeratorObject(BaseObject, c.CEnumeratorObject):
    pass


class CMacroObject(BaseObject, c.CMacroObject):
    pass


# ----------------------------------------------------------------------------


class PyFunction(BaseObject, python.PyFunction):
    pass


class PyAttribute(BaseObject, python.PyAttribute):
    pass


class PyClasslike(BaseObject, python.PyClasslike):
    pass


# ----------------------------------------------------------------------------

# Create multi-inheritance classes to merge BaseObject from Breathe with
# classes from phpdomain.
# We use capitalization (and the namespace) to differentiate between the two

if php is not None:

    class PHPNamespaceLevel(BaseObject, php.PhpNamespacelevel):
        """Description of a PHP item *in* a namespace (not the space itself)."""

        pass

    class PHPClassLike(BaseObject, php.PhpClasslike):
        pass

    class PHPClassMember(BaseObject, php.PhpClassmember):
        pass

    class PHPGlobalLevel(BaseObject, php.PhpGloballevel):
        pass


# ----------------------------------------------------------------------------

if cs is not None:

    class CSharpCurrentNamespace(BaseObject, cs.CSharpCurrentNamespace):
        pass

    class CSharpNamespacePlain(BaseObject, cs.CSharpNamespacePlain):
        pass

    class CSharpClass(BaseObject, cs.CSharpClass):
        pass

    class CSharpStruct(BaseObject, cs.CSharpStruct):
        pass

    class CSharpInterface(BaseObject, cs.CSharpInterface):
        pass

    class CSharpInherits(BaseObject, cs.CSharpInherits):
        pass

    class CSharpMethod(BaseObject, cs.CSharpMethod):
        pass

    class CSharpVariable(BaseObject, cs.CSharpVariable):
        pass

    class CSharpProperty(BaseObject, cs.CSharpProperty):
        pass

    class CSharpEvent(BaseObject, cs.CSharpEvent):
        pass

    class CSharpEnum(BaseObject, cs.CSharpEnum):
        pass

    class CSharpEnumValue(BaseObject, cs.CSharpEnumValue):
        pass

    class CSharpAttribute(BaseObject, cs.CSharpAttribute):
        pass

    class CSharpIndexer(BaseObject, cs.CSharpIndexer):
        pass

    class CSharpXRefRole(BaseObject, cs.CSharpXRefRole):
        pass


# ----------------------------------------------------------------------------


class DomainDirectiveFactory:
    # A mapping from node kinds to domain directives and their names.
    cpp_classes = {
        "variable": (CPPMemberObject, "var"),
        "class": (CPPClassObject, "class"),
        "struct": (CPPClassObject, "struct"),
        "interface": (CPPClassObject, "class"),
        "function": (CPPFunctionObject, "function"),
        "friend": (CPPFunctionObject, "function"),
        "signal": (CPPFunctionObject, "function"),
        "slot": (CPPFunctionObject, "function"),
        "concept": (CPPConceptObject, "concept"),
        "enum": (CPPEnumObject, "enum"),
        "enum-class": (CPPEnumObject, "enum-class"),
        "typedef": (CPPTypeObject, "type"),
        "using": (CPPTypeObject, "type"),
        "union": (CPPUnionObject, "union"),
        "namespace": (CPPTypeObject, "type"),
        "enumvalue": (CPPEnumeratorObject, "enumerator"),
        "define": (CMacroObject, "macro"),
    }
    c_classes = {
        "variable": (CMemberObject, "var"),
        "function": (CFunctionObject, "function"),
        "define": (CMacroObject, "macro"),
        "struct": (CStructObject, "struct"),
        "union": (CUnionObject, "union"),
        "enum": (CEnumObject, "enum"),
        "enumvalue": (CEnumeratorObject, "enumerator"),
        "typedef": (CTypeObject, "type"),
    }
    python_classes = {
        # TODO: PyFunction is meant for module-level functions
        #       and PyAttribute is meant for class attributes, not module-level variables.
        #       Somehow there should be made a distinction at some point to get the correct
        #       index-text and whatever other things are different.
        "function": (PyFunction, "function"),
        "variable": (PyAttribute, "attribute"),
        "class": (PyClasslike, "class"),
        "namespace": (PyClasslike, "class"),
    }

    if php is not None:
        php_classes = {
            "function": (PHPNamespaceLevel, "function"),
            "class": (PHPClassLike, "class"),
            "attr": (PHPClassMember, "attr"),
            "method": (PHPClassMember, "method"),
            "global": (PHPGlobalLevel, "global"),
        }
        php_classes_default = php_classes["class"]  # Directive when no matching ones were found

    if cs is not None:
        cs_classes = {
            # 'doxygen-name': (CSharp class, key in CSharpDomain.object_types)
            "namespace": (CSharpNamespacePlain, "namespace"),
            "class": (CSharpClass, "class"),
            "struct": (CSharpStruct, "struct"),
            "interface": (CSharpInterface, "interface"),
            "function": (CSharpMethod, "function"),
            "method": (CSharpMethod, "method"),
            "variable": (CSharpVariable, "var"),
            "property": (CSharpProperty, "property"),
            "event": (CSharpEvent, "event"),
            "enum": (CSharpEnum, "enum"),
            "enumvalue": (CSharpEnumValue, "enumerator"),
            "attribute": (CSharpAttribute, "attr"),
            # Fallback to cpp domain
            "typedef": (CPPTypeObject, "type"),
        }

    @staticmethod
    def create(domain: str, args) -> ObjectDescription:
        cls: Type[ObjectDescription]
        name: str
        if domain == "c":
            cls, name = DomainDirectiveFactory.c_classes[args[0]]
        elif domain == "py":
            cls, name = DomainDirectiveFactory.python_classes[args[0]]
        elif php is not None and domain == "php":
            separators = php.separators
            arg_0 = args[0]
            if any([separators["method"] in n for n in args[1]]):
                if any([separators["attr"] in n for n in args[1]]):
                    arg_0 = "attr"
                else:
                    arg_0 = "method"
            else:
                if arg_0 in ["variable"]:
                    arg_0 = "global"

            if arg_0 in DomainDirectiveFactory.php_classes:
                cls, name = DomainDirectiveFactory.php_classes[arg_0]  # type: ignore
            else:
                cls, name = DomainDirectiveFactory.php_classes_default  # type: ignore

        elif cs is not None and domain == "cs":
            cls, name = DomainDirectiveFactory.cs_classes[args[0]]
        else:
            domain = "cpp"
            cls, name = DomainDirectiveFactory.cpp_classes[args[0]]  # type: ignore
        # Replace the directive name because domain directives don't know how to handle
        # Breathe's "doxygen" directives.
        assert ":" not in name
        args = [domain + ":" + name] + args[1:]
        return cls(*args)


class NodeFinder(nodes.SparseNodeVisitor):
    """Find the Docutils desc_signature declarator and desc_content nodes."""

    def __init__(self, document):
        super().__init__(document)
        self.declarator = None
        self.content = None

    def visit_desc_signature(self, node):
        # Find the last signature node because it contains the actual declarator
        # rather than "template <...>". In Sphinx 1.4.1 we'll be able to use sphinx_cpp_tagname:
        # https://github.com/michaeljones/breathe/issues/242
        self.declarator = node

    def visit_desc_signature_line(self, node):
        # In sphinx 1.5, there is now a desc_signature_line node within the desc_signature
        # This should be used instead
        self.declarator = node

    def visit_desc_content(self, node):
        self.content = node
        # The SparseNodeVisitor seems to not actually be universally Sparse,
        # but only for nodes known to Docutils.
        # So if there are extensions with new node types in the content,
        # then the visitation will fail.
        # We anyway don't need to visit the actual content, so skip it.
        raise nodes.SkipChildren


def intersperse(iterable, delimiter):
    it = iter(iterable)
    yield next(it)
    for x in it:
        yield delimiter
        yield x


def get_param_decl(param: parser.Node_paramType) -> str:
    def to_string(node: parser.Node_linkedTextType | None) -> str:
        """Convert Doxygen node content to a string."""
        result: list[str] = []
        if node is not None:
            for p in node:
                if isinstance(p, str):
                    result.append(p)
                else:
                    result.append(p.value[0])
        return " ".join(result)

    param_type = to_string(param.type)
    param_name = param.declname if param.declname else param.defname
    if not param_name:
        param_decl = param_type
    else:
        param_decl, number_of_subs = re.subn(
            r"(\((?:\w+::)*[*&]+)(\))", r"\g<1>" + param_name + r"\g<2>", param_type
        )
        if number_of_subs == 0:
            param_decl = param_type + " " + param_name
    if param.array:
        param_decl += param.array
    if param.defval:
        param_decl += " = " + to_string(param.defval)

    return param_decl


def get_definition_without_template_args(data_object):
    """
    Return data_object.definition removing any template arguments from the class name in the member
    function.  Otherwise links to classes defined in the same template are not generated correctly.

    For example in 'Result<T> A< B<C> >::f' we want to remove the '< B<C> >' part.
    """
    definition = data_object.definition
    if len(data_object.bitfield) > 0:
        definition += " : " + data_object.bitfield
    qual_name = "::" + data_object.name
    if definition.endswith(qual_name):
        qual_name_start = len(definition) - len(qual_name)
        pos = qual_name_start - 1
        if definition[pos] == ">":
            bracket_count = 0
            # Iterate back through the characters of the definition counting matching braces and
            # then remove all braces and everything between
            while pos > 0:
                if definition[pos] == ">":
                    bracket_count += 1
                elif definition[pos] == "<":
                    bracket_count -= 1
                    if bracket_count == 0:
                        definition = definition[:pos] + definition[qual_name_start:]
                        break
                pos -= 1
    return definition


class InlineText(Text):
    """
    Add a custom docutils class to allow parsing inline text. This is to be
    used inside a @verbatim/@endverbatim block but only the first line is
    consumed and a inline element is generated as the parent, instead of the
    paragraph used by Text.
    """

    patterns = {"inlinetext": r""}
    initial_transitions = [("inlinetext",)]

    def indent(self, match, context, next_state):
        """
        Avoid Text's indent from detecting space prefixed text and
        doing "funny" stuff; always rely on inlinetext for parsing.
        """
        return self.inlinetext(match, context, next_state)

    def eof(self, context):
        """
        Text.eof() inserts a paragraph, so override it to skip adding elements.
        """
        return []

    def inlinetext(self, match, context, next_state):
        """
        Called by the StateMachine when an inline element is found (which is
        any text when this class is added as the single transition.
        """
        startline = self.state_machine.abs_line_number() - 1
        msg = None
        try:
            block = self.state_machine.get_text_block()
        except UnexpectedIndentationError as err:
            block, src, srcline = err.args
            msg = self.reporter.error("Unexpected indentation.", source=src, line=srcline)
        lines = context + list(block)
        text, _ = self.inline_text(lines[0], startline)
        self.parent += text
        self.parent += msg
        return [], next_state, []


class SphinxRenderer:
    """
    Doxygen node visitor that converts input into Sphinx/RST representation.
    Each visit method takes a Doxygen node as an argument and returns a list of RST nodes.
    """

    def __init__(
        self,
        app: Sphinx,
        project_info: ProjectInfo,
        node_stack,
        state,
        document: nodes.document,
        target_handler: TargetHandler,
        compound_parser: parser.DoxygenCompoundParser,
        filter_: Filter,
    ):
        self.app = app

        self.project_info = project_info
        self.qualification_stack = node_stack
        self.nesting_level = 0
        self.state = state
        self.document = document
        self.target_handler = target_handler
        self.compound_parser = compound_parser
        self.filter_ = filter_

        self.context: Optional[RenderContext] = None
        self.output_defname = True
        # Nesting level for lists.
        self.nesting_level = 0

    def set_context(self, context: RenderContext) -> None:
        self.context = context
        if self.context.domain == "":
            self.context.domain = self.get_domain()

    # XXX: fix broken links in XML generated by Doxygen when Doxygen's
    # SEPARATE_MEMBER_PAGES is set to YES; this function should be harmless
    # when SEPARATE_MEMBER_PAGES is NO!
    #
    # The issue was discussed here: https://github.com/doxygen/doxygen/pull/7971
    #
    # A Doxygen anchor consists of a 32-byte string version of the results of
    # passing in the stringified identifier or prototype that is being "hashed".
    # An "a" character is then prefixed to mark it as an anchor. Depending on how
    # the identifier is linked, it can also get a "g" prefix to mean it is part
    # of a Doxygen group. This results in an id having either 33 or 34 bytes
    # (containing a "g" or not). Some identifiers, eg enumerators, get twice that
    # length to have both a unique enum + unique enumerator, and sometimes they
    # get two "g" characters as prefix instead of one.
    def _fixup_separate_member_pages(self, refid: str) -> str:
        if refid:
            parts = refid.rsplit("_", 1)
            if len(parts) == 2 and parts[1].startswith("1"):
                anchorid = parts[1][1:]
                if len(anchorid) in set([33, 34]) and parts[0].endswith(anchorid):
                    return parts[0][: -len(anchorid)] + parts[1]
                elif len(anchorid) > 34:
                    index = 0
                    if anchorid.startswith("gg"):
                        index = 1
                        _len = 35
                    elif anchorid.startswith("g"):
                        _len = 34
                    else:
                        _len = 33
                    if parts[0].endswith(anchorid[index:_len]):
                        return parts[0][: -(_len - index)] + parts[1]

        return refid

    def get_refid(self, refid: str) -> str:
        if self.app.config.breathe_separate_member_pages:
            refid = self._fixup_separate_member_pages(refid)
        if self.app.config.breathe_use_project_refids:
            return "%s%s" % (self.project_info.name(), refid)
        else:
            return refid

    def get_domain(self) -> str:
        """Returns the domain for the current node."""

        def get_filename(node) -> Optional[str]:
            """Returns the name of a file where the declaration represented by node is located."""
            try:
                return node.location.file
            except AttributeError:
                return None

        assert self.context is not None
        node_stack = self.context.node_stack
        node = node_stack[0]
        # An enumvalueType node doesn't have location, so use its parent node
        # for detecting the domain instead.
        if isinstance(node, (str,parser.Node_enumvalueType)):
            node = node_stack[1]
        filename = get_filename(node)
        if not filename and node.node_type == "compound":
            file_data = self.compound_parser.parse(node.refid)
            filename = get_filename(file_data.compounddef)
        return self.project_info.domain_for_file(filename) if filename else ""

    def join_nested_name(self, names: list[str]) -> str:
        dom = self.get_domain()
        sep = "::" if not dom or dom == "cpp" else "."
        return sep.join(names)

    def run_directive(
        self, obj_type: str, declaration: str, contentCallback: ContentCallback, options={}
    ) -> list[Node]:
        assert self.context is not None
        args = [obj_type, [declaration]] + self.context.directive_args[2:]
        directive = DomainDirectiveFactory.create(self.context.domain, args)
        assert issubclass(type(directive), BaseObject)
        directive.breathe_content_callback = contentCallback  # type: ignore

        # Translate Breathe's no-link option into the standard noindex option.
        if "no-link" in self.context.directive_args[2]:
            directive.options["noindex"] = True
        for k, v in options.items():
            directive.options[k] = v

        assert self.app.env is not None
        config = self.app.env.config

        if config.breathe_debug_trace_directives:
            global _debug_indent
            print(
                "{}Running directive: .. {}:: {}".format(
                    "  " * _debug_indent, directive.name, declaration
                )
            )
            _debug_indent += 1

        self.nesting_level += 1
        nodes = directive.run()
        self.nesting_level -= 1

        # TODO: the directive_args seems to be reused between different run_directives
        #       so for now, reset the options.
        #       Remove this once the args are given in a different manner.
        for k, v in options.items():
            del directive.options[k]

        if config.breathe_debug_trace_directives:
            _debug_indent -= 1

        # Filter out outer class names if we are rendering a member as a part of a class content.
        # In some cases of errors with a declaration there are no nodes
        # (e.g., variable in function), so perhaps skip (see #671).
        # If there are nodes, there should be at least 2.
        if len(nodes) != 0:
            assert len(nodes) >= 2, nodes
            rst_node = nodes[1]
            finder = NodeFinder(rst_node.document)
            rst_node.walk(finder)

            signode = finder.declarator

            if self.context.child:
                signode.children = [n for n in signode.children if not n.tagname == "desc_addname"]
        return nodes

    def handle_declaration(
        self,
        node,
        declaration: str,
        *,
        obj_type: Optional[str] = None,
        content_callback: Optional[ContentCallback] = None,
        display_obj_type: Optional[str] = None,
        declarator_callback: Optional[DeclaratorCallback] = None,
        options={},
    ) -> list[Node]:
        if obj_type is None:
            obj_type = node.kind
        if content_callback is None:

            def content(contentnode):
                contentnode.extend(self.description(node))

            content_callback = content
        declaration = declaration.replace("\n", " ")
        nodes_ = self.run_directive(obj_type, declaration, content_callback, options)

        assert self.app.env is not None
        if self.app.env.config.breathe_debug_trace_doxygen_ids:
            target = self.create_doxygen_target(node)
            if len(target) == 0:
                print("{}Doxygen target: (none)".format("  " * _debug_indent))
            else:
                print("{}Doxygen target: {}".format("  " * _debug_indent, target[0]["ids"]))

        # <desc><desc_signature> and then one or more <desc_signature_line>
        # each <desc_signature_line> has a sphinx_line_type which hints what is present in that line
        # In some cases of errors with a declaration there are no nodes
        # (e.g., variable in function), so perhaps skip (see #671).
        if len(nodes_) == 0:
            return []
        assert len(nodes_) >= 2, nodes_
        desc = nodes_[1]
        assert isinstance(desc, addnodes.desc)
        assert len(desc) >= 1
        sig = desc[0]
        assert isinstance(sig, addnodes.desc_signature)
        # if may or may not be a multiline signature
        isMultiline = sig.get("is_multiline", False)
        declarator: Optional[Declarator] = None
        if isMultiline:
            for line in sig:
                assert isinstance(line, addnodes.desc_signature_line)
                if line.sphinx_line_type == "declarator":
                    declarator = line
        else:
            declarator = sig
        assert declarator is not None
        if display_obj_type is not None:
            n = declarator[0]
            newStyle = True
            # the new style was introduced in Sphinx v4
            if sphinx.version_info[0] < 4:
                newStyle = False
            # but only for the C and C++ domains
            if self.get_domain() and self.get_domain() not in ("c", "cpp"):
                newStyle = False
            if newStyle:
                assert isinstance(n, addnodes.desc_sig_keyword)
                declarator[0] = addnodes.desc_sig_keyword(display_obj_type, display_obj_type)
            else:
                assert isinstance(n, addnodes.desc_annotation)
                assert n.astext()[-1] == " "
                txt = display_obj_type + " "
                declarator[0] = addnodes.desc_annotation(txt, txt)
        if not self.app.env.config.breathe_debug_trace_doxygen_ids:
            target = self.create_doxygen_target(node)
        declarator.insert(0, target)
        if declarator_callback:
            declarator_callback(declarator)
        return nodes_

    def get_qualification(self) -> list[str]:
        if self.nesting_level > 0:
            return []

        assert self.app.env is not None
        config = self.app.env.config
        if config.breathe_debug_trace_qualification:

            def debug_print_node(n):
                return "node_type={}".format(n.node_type)

            global _debug_indent
            print(
                "{}{}".format(_debug_indent * "  ", debug_print_node(self.qualification_stack[0]))
            )
            _debug_indent += 1

        names: list[str] = []
        for node in self.qualification_stack[1:]:
            if config.breathe_debug_trace_qualification:
                print("{}{}".format(_debug_indent * "  ", debug_print_node(node)))
            if node.node_type == "ref" and len(names) == 0:
                if config.breathe_debug_trace_qualification:
                    print("{}{}".format(_debug_indent * "  ", "res="))
                return []
            if (
                node.node_type == "compound" and node.kind not in ["file", "namespace", "group"]
            ) or node.node_type == "memberdef":
                # We skip the 'file' entries because the file name doesn't form part of the
                # qualified name for the identifier. We skip the 'namespace' entries because if we
                # find an object through the namespace 'compound' entry in the index.xml then we'll
                # also have the 'compounddef' entry in our node stack and we'll get it from that. We
                # need the 'compounddef' entry because if we find the object through the 'file'
                # entry in the index.xml file then we need to get the namespace name from somewhere
                names.append(node.name)
            if node.node_type == "compounddef" and node.kind == "namespace":
                # Nested namespaces include their parent namespace(s) in compoundname. ie,
                # compoundname is 'foo::bar' instead of just 'bar' for namespace 'bar' nested in
                # namespace 'foo'. We need full compoundname because node_stack doesn't necessarily
                # include parent namespaces and we stop here in case it does.
                names.extend(reversed(node.compoundname.split("::")))
                break

        names.reverse()

        if config.breathe_debug_trace_qualification:
            print("{}res={}".format(_debug_indent * "  ", names))
            _debug_indent -= 1
        return names

    # ===================================================================================

    def get_fully_qualified_name(self):

        names = []
        node_stack = self.context.node_stack
        node = node_stack[0]

        # If the node is a namespace, use its name because namespaces are skipped in the main loop.
        if node.node_type == "compound" and node.kind == "namespace":
            names.append(node.name)

        for node in node_stack:
            if node.node_type == "ref" and len(names) == 0:
                return node.valueOf_
            if (
                node.node_type == "compound" and node.kind not in ["file", "namespace", "group"]
            ) or node.node_type == "memberdef":
                # We skip the 'file' entries because the file name doesn't form part of the
                # qualified name for the identifier. We skip the 'namespace' entries because if we
                # find an object through the namespace 'compound' entry in the index.xml then we'll
                # also have the 'compounddef' entry in our node stack and we'll get it from that. We
                # need the 'compounddef' entry because if we find the object through the 'file'
                # entry in the index.xml file then we need to get the namespace name from somewhere
                names.insert(0, node.name)
            if node.node_type == "compounddef" and node.kind == "namespace":
                # Nested namespaces include their parent namespace(s) in compoundname. ie,
                # compoundname is 'foo::bar' instead of just 'bar' for namespace 'bar' nested in
                # namespace 'foo'. We need full compoundname because node_stack doesn't necessarily
                # include parent namespaces and we stop here in case it does.
                names.insert(0, node.compoundname)
                break

        return "::".join(names)

    def create_template_prefix(self, decl) -> str:
        if not decl.templateparamlist:
            return ""
        nodes = self.render(decl.templateparamlist)
        return "template<" + "".join(n.astext() for n in nodes) + ">"

    def run_domain_directive(self, kind, names):
        domain_directive = DomainDirectiveFactory.create(
            self.context.domain, [kind, names] + self.context.directive_args[2:]
        )

        # Translate Breathe's no-link option into the standard noindex option.
        if "no-link" in self.context.directive_args[2]:
            domain_directive.options["noindex"] = True

        config = self.app.env.config
        if config.breathe_debug_trace_directives:
            global _debug_indent
            print(
                "{}Running directive (old): .. {}:: {}".format(
                    "  " * _debug_indent, domain_directive.name, "".join(names)
                )
            )
            _debug_indent += 1

        nodes = domain_directive.run()

        if config.breathe_debug_trace_directives:
            _debug_indent -= 1

        # Filter out outer class names if we are rendering a member as a part of a class content.
        rst_node = nodes[1]
        finder = NodeFinder(rst_node.document)
        rst_node.walk(finder)

        signode = finder.declarator

        if len(names) > 0 and self.context.child:
            signode.children = [n for n in signode.children if not n.tagname == "desc_addname"]
        return nodes

    def create_doxygen_target(self, node):
        """Can be overridden to create a target node which uses the doxygen refid information
        which can be used for creating links between internal doxygen elements.

        The default implementation should suffice most of the time.
        """

        refid = self.get_refid(node.id)
        return self.target_handler.create_target(refid)

    def title(self, node) -> list[Node]:
        nodes_ = []

        # Variable type or function return type
        nodes_.extend(self.render_optional(node.type_))
        if nodes_:
            nodes_.append(nodes.Text(" "))
        nodes_.append(addnodes.desc_name(text=node.name))
        return nodes_

    def description(self, node) -> list[Node]:
        brief = self.render_optional(node.briefdescription)
        detailed = self.detaileddescription(node)
        return brief + detailed

    def detaileddescription(self, node) -> list[Node]:
        detailedCand = self.render_optional(node.detaileddescription)
        # all field_lists must be at the top-level of the desc_content, so pull them up
        fieldLists: list[nodes.field_list] = []
        admonitions: list[Node] = []

        def pullup(node, typ, dest):
            for n in node.traverse(typ):
                del n.parent[n.parent.index(n)]
                dest.append(n)

        detailed = []
        for candNode in detailedCand:
            pullup(candNode, nodes.field_list, fieldLists)
            pullup(candNode, nodes.note, admonitions)
            pullup(candNode, nodes.warning, admonitions)
            # and collapse paragraphs
            for para in candNode.traverse(nodes.paragraph):
                if (
                    para.parent
                    and len(para.parent) == 1
                    and isinstance(para.parent, nodes.paragraph)
                ):
                    para.replace_self(para.children)

            # and remove empty top-level paragraphs
            if isinstance(candNode, nodes.paragraph) and len(candNode) == 0:
                continue
            detailed.append(candNode)

        # make one big field list instead to the Sphinx transformer can make it pretty
        if len(fieldLists) > 1:
            fieldList = nodes.field_list()
            for fl in fieldLists:
                fieldList.extend(fl)
            fieldLists = [fieldList]

        # collapse retvals into a single return field
        if len(fieldLists) != 0 and sphinx.version_info[0:2] < (4, 3):
            others: list[nodes.field] = []
            retvals: list[nodes.field] = []
            f: nodes.field
            fn: nodes.field_name
            fb: nodes.field_body
            for f in fieldLists[0]:
                fn, fb = f
                assert len(fn) == 1
                if fn.astext().startswith("returns "):
                    retvals.append(f)
                else:
                    others.append(f)
            if len(retvals) != 0:
                items: list[nodes.paragraph] = []
                for fn, fb in retvals:
                    # we created the retvals before, so we made this prefix
                    assert fn.astext().startswith("returns ")
                    val = nodes.strong("", fn.astext()[8:])
                    # assumption from visit_Node_docParamListType: fb is a single paragraph or nothing
                    assert len(fb) <= 1, fb
                    bodyNodes = [val, nodes.Text(" -- ")]
                    if len(fb) == 1:
                        assert isinstance(fb[0], nodes.paragraph)
                        bodyNodes.extend(fb[0])
                    items.append(nodes.paragraph("", "", *bodyNodes))
                # only make a bullet list if there are multiple retvals
                body: Node
                if len(items) == 1:
                    body = items[0]
                else:
                    body = nodes.bullet_list()
                    for i in items:
                        body.append(nodes.list_item("", i))
                fRetvals = nodes.field(
                    "", nodes.field_name("", "returns"), nodes.field_body("", body)
                )
                fl = nodes.field_list("", *others, fRetvals)
                fieldLists = [fl]

        if self.app.config.breathe_order_parameters_first:
            return detailed + fieldLists + admonitions
        else:
            return detailed + admonitions + fieldLists

    def update_signature(self, signature, obj_type):
        """Update the signature node if necessary, e.g. add qualifiers."""
        prefix = obj_type + " "
        annotation = addnodes.desc_annotation(prefix, prefix)
        if signature[0].tagname != "desc_name":
            signature[0] = annotation
        else:
            signature.insert(0, annotation)

    def render_declaration(self, node, declaration=None, description=None, **kwargs):
        if declaration is None:
            declaration = self.get_fully_qualified_name()
        obj_type = kwargs.get("objtype", None)
        if obj_type is None:
            obj_type = node.kind
        nodes = self.run_domain_directive(obj_type, [declaration.replace("\n", " ")])
        if self.app.env.config.breathe_debug_trace_doxygen_ids:
            target = self.create_doxygen_target(node)
            if len(target) == 0:
                print("{}Doxygen target (old): (none)".format("  " * _debug_indent))
            else:
                print("{}Doxygen target (old): {}".format("  " * _debug_indent, target[0]["ids"]))

        rst_node = nodes[1]
        finder = NodeFinder(rst_node.document)
        rst_node.walk(finder)

        signode = finder.declarator
        contentnode = finder.content

        update_signature = kwargs.get("update_signature", None)
        if update_signature is not None:
            update_signature(signode, obj_type)
        if description is None:
            description = self.description(node)
        if not self.app.env.config.breathe_debug_trace_doxygen_ids:
            target = self.create_doxygen_target(node)
        signode.insert(0, target)
        contentnode.extend(description)
        return nodes

    def visit_Node_DoxygenTypeIndex(self, node: parser.Node_DoxygenTypeIndex) -> list[Node]:
        nodelist: list[Node] = []

        # Process all the compound children
        for n in node.compound:
            nodelist.extend(self.render(n))
        return nodelist

    def visit_Node_DoxygenType(self, node: parser.Node_DoxygenType) -> list[Node]:
        assert len(node.compounddef) == 1
        return self.render(node.compounddef[0])

    def visit_union(self, node) -> list[Node]:
        # Read in the corresponding xml file and process
        file_data = self.compound_parser.parse(node.refid)
        nodeDef = file_data.compounddef

        assert self.context is not None
        parent_context = self.context.create_child_context(file_data)
        new_context = parent_context.create_child_context(nodeDef)

        with WithContext(self, new_context):
            names = self.get_qualification()
            if self.nesting_level == 0:
                names.extend(nodeDef.compoundname.split("::"))
            else:
                names.append(nodeDef.compoundname.split("::")[-1])
            declaration = self.join_nested_name(names)

            def content(contentnode):
                if nodeDef.includes:
                    for include in nodeDef.includes:
                        contentnode.extend(
                            self.render(include, new_context.create_child_context(include))
                        )
                rendered_data = self.render(file_data, parent_context)
                contentnode.extend(rendered_data)

            nodes = self.handle_declaration(nodeDef, declaration, content_callback=content)
        return nodes

    def visit_class(self, node) -> list[Node]:
        # Read in the corresponding xml file and process
        file_data = self.compound_parser.parse(node.refid)
        nodeDef = file_data.compounddef

        assert self.context is not None
        parent_context = self.context.create_child_context(file_data)
        new_context = parent_context.create_child_context(nodeDef)

        domain = self.get_domain()

        with WithContext(self, new_context):
            # Pretend that the signature is being rendered in context of the
            # definition, for proper domain detection
            kind = nodeDef.kind
            # Defer to domains specific directive.

            names = self.get_qualification()
            # TODO: this breaks if it's a template specialization
            #       and one of the arguments contain '::'
            if self.nesting_level == 0:
                names.extend(nodeDef.compoundname.split("::"))
            else:
                names.append(nodeDef.compoundname.split("::")[-1])
            decls = [
                self.create_template_prefix(nodeDef),
                self.join_nested_name(names),
            ]
            # add base classes
            if len(nodeDef.basecompoundref) != 0:
                decls.append(":")
            first = True
            for base in nodeDef.basecompoundref:
                if not first:
                    decls.append(",")
                else:
                    first = False
                if base.prot is not None and domain != "cs":
                    decls.append(base.prot)
                if base.virt == "virtual":
                    decls.append("virtual")
                decls.append(base.content_[0].value)
            declaration = " ".join(decls)

            def content(contentnode) -> None:
                if nodeDef.includes:
                    for include in nodeDef.includes:
                        contentnode.extend(
                            self.render(include, new_context.create_child_context(include))
                        )
                rendered_data = self.render(file_data, parent_context)
                contentnode.extend(rendered_data)

            assert kind in ("class", "struct", "interface")
            display_obj_type = "interface" if kind == "interface" else None
            nodes = self.handle_declaration(
                nodeDef, declaration, content_callback=content, display_obj_type=display_obj_type
            )
            if "members-only" in self.context.directive_args[2]:
                assert len(nodes) >= 2
                assert isinstance(nodes[1], addnodes.desc)
                assert len(nodes[1]) >= 2
                assert isinstance(nodes[1][1], addnodes.desc_content)
                return nodes[1][1].children
        return nodes

    def visit_namespace(self, node) -> list[Node]:
        # Read in the corresponding xml file and process
        file_data = self.compound_parser.parse(node.refid)
        nodeDef = file_data.compounddef

        assert self.context is not None
        parent_context = self.context.create_child_context(file_data)
        new_context = parent_context.create_child_context(file_data.compounddef)

        with WithContext(self, new_context):
            # Pretend that the signature is being rendered in context of the
            # definition, for proper domain detection
            names = self.get_qualification()
            if self.nesting_level == 0:
                names.extend(nodeDef.compoundname.split("::"))
            else:
                names.append(nodeDef.compoundname.split("::")[-1])
            declaration = self.join_nested_name(names)

            def content(contentnode):
                if nodeDef.includes:
                    for include in nodeDef.includes:
                        contentnode.extend(
                            self.render(include, new_context.create_child_context(include))
                        )
                rendered_data = self.render(file_data, parent_context)
                contentnode.extend(rendered_data)

            display_obj_type = "namespace" if self.get_domain() != "py" else "module"
            nodes = self.handle_declaration(
                nodeDef, declaration, content_callback=content, display_obj_type=display_obj_type
            )
        return nodes

    def visit_compound(self, node, render_empty_node=True, **kwargs) -> list[Node]:
        # Read in the corresponding xml file and process
        file_data = self.compound_parser.parse(node.refid)

        def get_node_info(file_data):
            return node.name, node.kind

        name, kind = kwargs.get("get_node_info", get_node_info)(file_data)
        if kind == "union":
            dom = self.get_domain()
            assert not dom or dom in ("c", "cpp")
            return self.visit_union(node)
        elif kind in ("struct", "class", "interface"):
            dom = self.get_domain()
            if not dom or dom in ("c", "cpp", "py", "cs"):
                return self.visit_class(node)
        elif kind == "namespace":
            dom = self.get_domain()
            if not dom or dom in ("c", "cpp", "py", "cs"):
                return self.visit_namespace(node)

        assert self.context is not None
        parent_context = self.context.create_child_context(file_data)
        new_context = parent_context.create_child_context(file_data.compounddef)
        rendered_data = self.render(file_data, parent_context)

        if not rendered_data and not render_empty_node:
            return []

        def render_signature(file_data, doxygen_target, name, kind):
            # Defer to domains specific directive.

            templatePrefix = self.create_template_prefix(file_data.compounddef)
            arg = "%s %s" % (templatePrefix, self.get_fully_qualified_name())

            # add base classes
            if kind in ("class", "struct"):
                bs = []
                for base in file_data.compounddef.basecompoundref:
                    b = []
                    if base.prot is not None:
                        b.append(base.prot)
                    if base.virt == "virtual":
                        b.append("virtual")
                    b.append(base.content_[0].value)
                    bs.append(" ".join(b))
                if len(bs) != 0:
                    arg += " : "
                    arg += ", ".join(bs)

            self.context.directive_args[1] = [arg]

            nodes = self.run_domain_directive(kind, self.context.directive_args[1])
            rst_node = nodes[1]

            finder = NodeFinder(rst_node.document)
            rst_node.walk(finder)

            if kind in ("interface", "namespace"):
                # This is not a real C++ declaration type that Sphinx supports,
                # so we hax the replacement of it.
                finder.declarator[0] = addnodes.desc_annotation(kind + " ", kind + " ")

            rst_node.children[0].insert(0, doxygen_target)
            return nodes, finder.content

        refid = self.get_refid(node.refid)
        render_sig = kwargs.get("render_signature", render_signature)
        with WithContext(self, new_context):
            # Pretend that the signature is being rendered in context of the
            # definition, for proper domain detection
            nodes, contentnode = render_sig(
                file_data, self.target_handler.create_target(refid), name, kind
            )

        if file_data.compounddef.includes:
            for include in file_data.compounddef.includes:
                contentnode.extend(self.render(include, new_context.create_child_context(include)))

        contentnode.extend(rendered_data)
        return nodes

    def visit_file(self, node) -> list[Node]:
        def render_signature(file_data, doxygen_target, name, kind):
            assert self.context is not None
            options = self.context.directive_args[2]

            if "content-only" in options:
                rst_node = nodes.container()
            else:
                rst_node = addnodes.desc()

                # Build targets for linking
                targets = []
                targets.extend(doxygen_target)

                title_signode = addnodes.desc_signature()
                title_signode.extend(targets)

                # Set up the title
                title_signode.append(nodes.emphasis(text=kind))
                title_signode.append(nodes.Text(" "))
                title_signode.append(addnodes.desc_name(text=name))

                rst_node.append(title_signode)

            rst_node.document = self.state.document
            rst_node["objtype"] = kind
            rst_node["domain"] = self.get_domain() if self.get_domain() else "cpp"

            contentnode = addnodes.desc_content()
            rst_node.append(contentnode)

            return [rst_node], contentnode

        return self.visit_compound(node, render_signature=render_signature)

    # We store both the identified and appropriate title text here as we want to define the order
    # here and the titles for the SectionDefTypeSubRenderer but we don't want the repetition of
    # having two lists in case they fall out of sync
    #
    # If this list is edited, also change the sections option documentation for
    # the doxygen(auto)file directive in documentation/source/file.rst.
    sections = [
        (parser.DoxSectionKind.user_defined, "User Defined"),
        (parser.DoxSectionKind.public_type, "Public Types"),
        (parser.DoxSectionKind.public_func, "Public Functions"),
        (parser.DoxSectionKind.public_attrib, "Public Members"),
        (parser.DoxSectionKind.public_slot, "Public Slots"),
        (parser.DoxSectionKind.signal, "Signals"),
        (parser.DoxSectionKind.dcop_func, "DCOP Function"),
        (parser.DoxSectionKind.property, "Properties"),
        (parser.DoxSectionKind.event, "Events"),
        (parser.DoxSectionKind.public_static_func, "Public Static Functions"),
        (parser.DoxSectionKind.public_static_attrib, "Public Static Attributes"),
        (parser.DoxSectionKind.protected_type, "Protected Types"),
        (parser.DoxSectionKind.protected_func, "Protected Functions"),
        (parser.DoxSectionKind.protected_attrib, "Protected Attributes"),
        (parser.DoxSectionKind.protected_slot, "Protected Slots"),
        (parser.DoxSectionKind.protected_static_func, "Protected Static Functions"),
        (parser.DoxSectionKind.protected_static_attrib, "Protected Static Attributes"),
        (parser.DoxSectionKind.package_type, "Package Types"),
        (parser.DoxSectionKind.package_func, "Package Functions"),
        (parser.DoxSectionKind.package_attrib, "Package Attributes"),
        (parser.DoxSectionKind.package_static_func, "Package Static Functions"),
        (parser.DoxSectionKind.package_static_attrib, "Package Static Attributes"),
        (parser.DoxSectionKind.private_type, "Private Types"),
        (parser.DoxSectionKind.private_func, "Private Functions"),
        (parser.DoxSectionKind.private_attrib, "Private Members"),
        (parser.DoxSectionKind.private_slot, "Private Slots"),
        (parser.DoxSectionKind.private_static_func, "Private Static Functions"),
        (parser.DoxSectionKind.private_static_attrib, "Private Static Attributes"),
        (parser.DoxSectionKind.friend, "Friends"),
        (parser.DoxSectionKind.related, "Related"),
        (parser.DoxSectionKind.define, "Defines"),
        (parser.DoxSectionKind.prototype, "Prototypes"),
        (parser.DoxSectionKind.typedef, "Typedefs"),
        #(parser.DoxSectionKind.concept, "Concepts"),
        (parser.DoxSectionKind.enum, "Enums"),
        (parser.DoxSectionKind.func, "Functions"),
        (parser.DoxSectionKind.var, "Variables"),
    ]

    def render_iterable(self, iterable: Iterable) -> list[Node]:
        output: list[Node] = []
        for entry in iterable:
            output.extend(self.render(entry))
        return output

    def visit_Node_compounddefType(self, node: parser.Node_compounddefType) -> list[Node]:
        assert self.context is not None
        options = self.context.directive_args[2]
        section_order = None
        if "sections" in options:
            section_order = {sec: i for i, sec in enumerate(options["sections"].split(" "))}
        membergroup_order = None
        if "membergroups" in options:
            membergroup_order = {sec: i for i, sec in enumerate(options["membergroups"].split(" "))}
        nodemap: dict[int, list[Node]] = {}

        def addnode(kind, lam):
            if section_order is None:
                nodemap[len(nodemap)] = lam()
            elif kind in section_order:
                nodemap.setdefault(section_order[kind], []).extend(lam())

        if "members-only" not in options:
            if "allow-dot-graphs" in options:
                addnode("incdepgraph", lambda: self.render_optional(node.incdepgraph))
                addnode("invincdepgraph", lambda: self.render_optional(node.invincdepgraph))
                addnode(
                    "inheritancegraph", lambda: self.render_optional(node.inheritancegraph)
                )
                addnode(
                    "collaborationgraph",
                    lambda: self.render_optional(node.collaborationgraph),
                )

            addnode("briefdescription", lambda: self.render_optional(node.briefdescription))
            addnode("detaileddescription", lambda: self.detaileddescription(node))

            def render_derivedcompoundref(node):
                if node is None:
                    return []
                output = self.render_iterable(node)
                if not output:
                    return []
                return [
                    nodes.paragraph(
                        "", "", nodes.Text("Subclassed by "), *intersperse(output, nodes.Text(", "))
                    )
                ]

            addnode(
                "derivedcompoundref", lambda: render_derivedcompoundref(node.derivedcompoundref)
            )

        section_nodelists: dict[str, list[Node]] = {}

        # Get all sub sections
        for sectiondef in node.sectiondef:
            kind = sectiondef.kind
            if section_order is not None and kind not in section_order:
                continue
            header = sectiondef.header
            if membergroup_order is not None and header not in membergroup_order:
                continue
            child_nodes = self.render(sectiondef)
            if not child_nodes:
                # Skip empty section
                continue
            rst_node = nodes.container(classes=["breathe-sectiondef"])
            rst_node.document = self.state.document
            rst_node["objtype"] = kind
            rst_node.extend(child_nodes)
            # We store the nodes as a list against the kind in a dictionary as the kind can be
            # 'user-edited' and that can repeat so this allows us to collect all the 'user-edited'
            # entries together
            section_nodelists.setdefault(kind.value, []).append(rst_node)

        # Order the results in an appropriate manner
        for kind, _ in self.sections:
            addnode(kind, lambda: section_nodelists.get(kind.value, []))

        # Take care of innerclasses
        addnode("innerclass", lambda: self.render_iterable(node.innerclass))
        addnode("innernamespace", lambda: self.render_iterable(node.innernamespace))

        if "inner" in options:
            for cnode in node.innergroup:
                file_data = self.compound_parser.parse(cnode.refid)
                assert len(file_data.compounddef) == 1
                inner = file_data.compounddef[0]
                addnode("innergroup", lambda: self.visit_Node_compounddefType(inner))

        nodelist = []
        for _, nodes_ in sorted(nodemap.items()):
            nodelist += nodes_

        return nodelist

    section_titles = dict(sections)

    def visit_Node_sectiondefType(self, node: parser.Node_sectiondefType) -> list[Node]:
        assert self.context is not None
        options = self.context.directive_args[2]
        node_list = []
        node_list.extend(self.render_optional(node.description))

        # Get all the memberdef info
        if "sort" in options:
            member_def = sorted(node.memberdef, key=lambda x: x.name)
        else:
            member_def = node.memberdef

        node_list.extend(self.render_iterable(member_def))

        if node_list:
            if "members-only" in options:
                return node_list

            text = self.section_titles[node.kind]
            # Override default name for user-defined sections. Use "Unnamed
            # Group" if the user didn't name the section
            # This is different to Doxygen which will track the groups and name
            # them Group1, Group2, Group3, etc.
            if node.kind == "user-defined":
                if node.header:
                    text = node.header
                else:
                    text = "Unnamed Group"

            # Use rubric for the title because, unlike the docutils element "section",
            # it doesn't interfere with the document structure.
            idtext = text.replace(" ", "-").lower()
            rubric = nodes.rubric(
                text=text,
                classes=["breathe-sectiondef-title"],
                ids=["breathe-section-title-" + idtext],
            )
            res: list[Node] = [rubric]
            return res + node_list
        return []

    def visit_Node_docRefTextType(self, node: parser.Node_docRefTextType|parser.Node_incType) -> list[Node]:
        nodelist = self.render_iterable(node)
        if hasattr(node, "para"):
            nodelist.extend(self.render_iterable(node.para))

        refid = self.get_refid(node.refid)

        nodelist = [
            addnodes.pending_xref(
                "",
                reftype="ref",
                refdomain="std",
                refexplicit=True,
                refid=refid,
                reftarget=refid,
                *nodelist,
            )
        ]
        return nodelist

    def visit_Node_docHeadingTyp(self, node: parser.Node_docHeadingType) -> list[Node]:
        """Heading renderer.

        Renders embedded headlines as emphasized text. Different heading levels
        are not supported.
        """
        nodelist = self.render_iterable(node)
        return [nodes.emphasis("", "", *nodelist)]

    def visit_Node_docParaType(self, node: parser.Node_docParaType) -> list[Node]:
        """
        <para> tags in the Doxygen output tend to contain either text or a single other tag of
        interest. So whilst it looks like we're combined descriptions and program listings and
        other things, in the end we generally only deal with one per para tag. Multiple
        neighbouring instances of these things tend to each be in a separate neighbouring para tag.
        """

        nodelist = []

        if self.context and self.context.directive_args[0] == "doxygenpage":
            nodelist.extend(self.render_iterable(node.ordered_children))
        else:
            contentNodeCands = self.render_iterable(node.content)
            # if there are consecutive nodes.Text we should collapse them
            # and rerender them to ensure the right paragraphifaction
            contentNodes: list[Node] = []
            for n in contentNodeCands:
                if len(contentNodes) != 0 and isinstance(contentNodes[-1], nodes.Text):
                    if isinstance(n, nodes.Text):
                        prev = contentNodes.pop()
                        contentNodes.extend(self.render_string(prev.astext() + n.astext()))
                        continue  # we have handled this node
                contentNodes.append(n)
            nodelist.extend(contentNodes)
            nodelist.extend(self.render_iterable(node.images))

            paramList = self.render_iterable(node.parameterlist)
            defs = []
            fields = []
            for n in self.render_iterable(node.simplesects):
                if isinstance(n, nodes.definition_list_item):
                    defs.append(n)
                elif isinstance(n, nodes.field_list):
                    fields.append(n)
                else:
                    nodelist.append(n)

            # note: all these gets pulled up and reordered in description()
            if len(defs) != 0:
                deflist = nodes.definition_list("", *defs)
                nodelist.append(deflist)
            nodelist.extend(paramList)
            nodelist.extend(fields)

        # And now all kinds of cleanup steps
        # ----------------------------------

        # trim trailing whitespace
        while len(nodelist) != 0:
            last = nodelist[-1]
            if not isinstance(last, nodes.Text):
                break
            if last.astext().strip() != "":
                break
            nodelist.pop()

        # https://github.com/michaeljones/breathe/issues/827
        # verbatim nodes should not be in a paragraph:
        if len(nodelist) == 1 and isinstance(nodelist[0], nodes.literal_block):
            return nodelist

        return [nodes.paragraph("", "", *nodelist)]

    visit_Node_docParBlockType = render_iterable

    def visit_Node_docBlockQuoteType(self, node: parser.Node_docBlockQuoteType) -> list[Node]:
        nodelist = self.render_iterable(node)
        # catch block quote attributions here; the <ndash/> tag is the only identifier,
        # and it is nested within a subsequent <para> tag
        if nodelist and nodelist[-1].astext().startswith("&#8212;"):
            # nodes.attribution prepends the author with an emphasized dash.
            # replace the &#8212; placeholder and strip any leading whitespace.
            text = nodelist[-1].astext().replace("&#8212;", "").lstrip()
            nodelist[-1] = nodes.attribution("", text)
        return [nodes.block_quote("", classes=[], *nodelist)]

    def visit_Node_docImageType(self, node: parser.Node_docImageType) -> list[Node]:
        """Output docutils image node using name attribute from xml as the uri"""

        path_to_image = node.name
        if path_to_image is None:
            path_to_image = ''
        elif not url_re.match(path_to_image):
            path_to_image = self.project_info.sphinx_abs_path_to_file(path_to_image)

        options = {"uri": path_to_image}
        return [nodes.image("", **options)]

    def visit_Node_docURLLink(self, node: parser.Node_docURLLink) -> list[Node]:
        """Url Link Renderer"""

        nodelist = self.render_iterable(node)
        return [nodes.reference("", "", refuri=node.url, *nodelist)]

    def visit_Node_docMarkupType(self, node: parser.Node_docMarkupType) -> list[Node]:
        nodelist = self.render_iterable(node)
        creator: Type[TextElement] = nodes.inline
        if node.type_ == "emphasis":
            creator = nodes.emphasis
        elif node.type_ == "computeroutput":
            creator = nodes.literal
        elif node.type_ == "bold":
            creator = nodes.strong
        elif node.type_ == "superscript":
            creator = nodes.superscript
        elif node.type_ == "subscript":
            creator = nodes.subscript
        elif node.type_ == "center":
            print("Warning: does not currently handle 'center' text display")
        elif node.type_ == "small":
            print("Warning: does not currently handle 'small' text display")
        return [creator("", "", *nodelist)]

    def visit_Node_docSect1Type(self, node: parser.Node_docSect1Type | parser.Node_docSect2Type | parser.Node_docSect3Type) -> list[Node]:
        """
        Docutils titles are defined by their level inside the document so
        the proper structure is only guaranteed by the Doxygen XML.

        Doxygen command mapping to XML element name:
        @section == sect1, @subsection == sect2, @subsubsection == sect3
        """
        section = nodes.section()
        section["ids"].append(self.get_refid(node.id))
        section += nodes.title(node.title, node.title)
        section += self.create_doxygen_target(node)
        section += self.render_iterable(node)
        return [section]
    
    visit_Node_docSect2Type = visit_Node_docSect1Type
    visit_Node_docSect3Type = visit_Node_docSect1Type

    def visit_Node_docSimpleSectType(self, node: parser.Node_docSimpleSectType) -> list[Node]:
        """Other Type documentation such as Warning, Note, Returns, etc"""

        # for those that should go into a field list, just render them as that,
        # and it will be pulled up later
        nodelist = self.render_iterable(node.para)

        if node.kind in ("pre", "post", "return"):
            return [
                nodes.field_list(
                    "",
                    nodes.field(
                        "",
                        nodes.field_name("", nodes.Text(node.kind)),
                        nodes.field_body("", *nodelist),
                    ),
                )
            ]
        elif node.kind == "warning":
            return [nodes.warning("", *nodelist)]
        elif node.kind == "note":
            return [nodes.note("", *nodelist)]
        elif node.kind == "see":
            return [addnodes.seealso("", *nodelist)]
        elif node.kind == "remark":
            nodelist.insert(0, nodes.title("", nodes.Text(node.kind.capitalize())))
            return [nodes.admonition("", classes=[node.kind], *nodelist)]

        if node.kind == "par":
            text = self.render(node.title)
        else:
            text = [nodes.Text(node.kind.capitalize())]
        # TODO: is this working as intended? there is something strange with the types
        title = nodes.strong("", "", *text)

        term = nodes.term("", "", title)
        definition = nodes.definition("", *nodelist)

        return [nodes.definition_list_item("", term, definition)]

    visit_Node_docTitleType = render_iterable

    def visit_Node_docFormulaType(self, node: parser.Node_docFormulaType) -> list[Node]:
        nodelist: list[Node] = []
        for item in node:
            latex = item.getValue()
            docname = self.state.document.settings.env.docname
            # Strip out the doxygen markup that slips through
            # Either inline
            if latex.startswith("$") and latex.endswith("$"):
                latex = latex[1:-1]
                nodelist.append(
                    nodes.math(text=latex, label=None, nowrap=False, docname=docname, number=None)
                )
            # Else we're multiline
            else:
                if latex.startswith("\\[") and latex.endswith("\\]"):
                    latex = latex[2:-2:]

                nodelist.append(
                    nodes.math_block(
                        text=latex, label=None, nowrap=False, docname=docname, number=None
                    )
                )
        return nodelist

    def visit_Node_listingType(self, node: parser.Node_listingType) -> list[Node]:
        nodelist: list[Node] = []
        for i, item in enumerate(node.codeline):
            # Put new lines between the lines
            if i:
                nodelist.append(nodes.Text("\n"))
            nodelist.extend(self.render(item))

        # Add blank string at the start otherwise for some reason it renders
        # the pending_xref tags around the kind in plain text
        block = nodes.literal_block("", "", *nodelist)
        if node.domain:
            block["language"] = node.domain
        return [block]

    visit_Node_codelineType = render_iterable

    visit_Node_highlightType = render_iterable

    def _nested_inline_parse_with_titles(self, content, node) -> str:
        """
        This code is basically a customized nested_parse_with_titles from
        docutils, using the InlineText class on the statemachine.
        """
        surrounding_title_styles = self.state.memo.title_styles
        surrounding_section_level = self.state.memo.section_level
        self.state.memo.title_styles = []
        self.state.memo.section_level = 0
        try:
            return self.state.nested_parse(
                content,
                0,
                node,
                match_titles=1,
                state_machine_kwargs={
                    "state_classes": (InlineText,),
                    "initial_state": "InlineText",
                },
            )
        finally:
            self.state.memo.title_styles = surrounding_title_styles
            self.state.memo.section_level = surrounding_section_level

    def visit_verbatim(self, node) -> list[Node]:
        if not node.text.strip().startswith("embed:rst"):
            # Remove trailing new lines. Purely subjective call from viewing results
            text = node.text.rstrip()

            # Handle has a preformatted text
            return [nodes.literal_block(text, text)]

        is_inline = False

        # do we need to strip leading asterisks?
        # NOTE: We could choose to guess this based on every line starting with '*'.
        #   However This would have a side-effect for any users who have an rst-block
        #   consisting of a simple bullet list.
        #   For now we just look for an extended embed tag
        if node.text.strip().startswith("embed:rst:leading-asterisk"):
            lines = node.text.splitlines()
            # Replace the first * on each line with a blank space
            lines = map(lambda text: text.replace("*", " ", 1), lines)
            node.text = "\n".join(lines)

        # do we need to strip leading ///?
        elif node.text.strip().startswith("embed:rst:leading-slashes"):
            lines = node.text.splitlines()
            # Replace the /// on each line with three blank spaces
            lines = map(lambda text: text.replace("///", "   ", 1), lines)
            node.text = "\n".join(lines)

        elif node.text.strip().startswith("embed:rst:inline"):
            # Inline all text inside the verbatim
            node.text = "".join(node.text.splitlines())
            is_inline = True

        if is_inline:
            text = node.text.replace("embed:rst:inline", "", 1)
        else:
            # Remove the first line which is "embed:rst[:leading-asterisk]"
            text = "\n".join(node.text.split("\n")[1:])

            # Remove starting whitespace
            text = textwrap.dedent(text)

        # Inspired by autodoc.py in Sphinx
        rst = StringList()
        for line in text.split("\n"):
            rst.append(line, "<breathe>")

        # Parent node for the generated node subtree
        rst_node: Node
        if is_inline:
            rst_node = nodes.inline()
        else:
            rst_node = nodes.paragraph()
        rst_node.document = self.state.document

        # Generate node subtree
        if is_inline:
            self._nested_inline_parse_with_titles(rst, rst_node)
        else:
            nested_parse_with_titles(self.state, rst, rst_node)

        return [rst_node]

    def visit_Node_incType(self, node: parser.Node_incType) -> list[Node]:
        if not self.app.config.breathe_show_include:
            return []

        compound_link: list[Node] = [nodes.Text(''.join(node))]
        if node.refid:
            compound_link = self.visit_Node_docRefTextType(node)
        if node.local == "yes":
            text = [nodes.Text('#include "'), *compound_link, nodes.Text('"')]
        else:
            text = [nodes.Text("#include <"), *compound_link, nodes.Text(">")]

        return [nodes.container("", nodes.emphasis("", "", *text))]

    def visit_Node_refType(self, node: parser.Node_refType) -> list[Node]:
        def get_node_info(file_data):
            name = ''.join(node)
            name = name.rsplit("::", 1)[-1]
            return name, file_data.compounddef.kind

        return self.visit_compound(node, False, get_node_info=get_node_info)

    def visit_Node_docListItemType(self, node: parser.Node_docListItemType) -> list[Node]:
        """List item renderer. Render all the children depth-first.
        Upon return expand the children node list into a docutils list-item.
        """
        nodelist = self.render_iterable(node)
        return [nodes.list_item("", *nodelist)]

    numeral_kind = ["arabic", "loweralpha", "lowerroman", "upperalpha", "upperroman"]

    def render_unordered(self, children) -> list[Node]:
        nodelist_list = nodes.bullet_list("", *children)
        return [nodelist_list]

    def render_enumerated(self, children, nesting_level) -> list[Node]:
        nodelist_list = nodes.enumerated_list("", *children)
        idx = nesting_level % len(SphinxRenderer.numeral_kind)
        nodelist_list["enumtype"] = SphinxRenderer.numeral_kind[idx]
        nodelist_list["prefix"] = ""
        nodelist_list["suffix"] = "."
        return [nodelist_list]

    def visit_Node_docListType(self, node: parser.Node_docListType) -> list[Node]:
        """List renderer

        The specifics of the actual list rendering are handled by the
        decorator around the generic render function.
        Render all the children depth-first."""
        """ Call the wrapped render function. Update the nesting level for the enumerated lists. """
        if node.node_subtype == "itemized":
            val = self.render_iterable(node.listitem)
            return self.render_unordered(children=val)
        elif node.node_subtype == "ordered":
            self.nesting_level += 1
            val = self.render_iterable(node.listitem)
            self.nesting_level -= 1
            return self.render_enumerated(children=val, nesting_level=self.nesting_level)
        return []

    def visit_Node_compoundRefType(self, node: parser.Node_compoundRefType) -> list[Node]:
        nodelist = self.render_iterable(node)
        refid = None
        if node.refid is not None:
            refid = self.get_refid(node.refid)
        if refid is not None:
            nodelist = [
                addnodes.pending_xref(
                    "",
                    reftype="ref",
                    refdomain="std",
                    refexplicit=True,
                    refid=refid,
                    reftarget=refid,
                    *nodelist,
                )
            ]
        return nodelist

    def visit_Node_docXRefSectType(self, node: parser.Node_docXRefSectType) -> list[Node]:
        assert self.app.env is not None

        signode = addnodes.desc_signature()
        title = node.xreftitle[0] + ":"
        titlenode = nodes.emphasis(text=title)
        ref = addnodes.pending_xref(
            "",
            reftype="ref",
            refdomain="std",
            refexplicit=True,
            reftarget=node.id,
            refdoc=self.app.env.docname,
            *[titlenode],
        )
        signode += ref

        nodelist = self.render(node.xrefdescription)
        contentnode = addnodes.desc_content()
        contentnode += nodelist

        descnode = addnodes.desc()
        descnode["objtype"] = "xrefsect"
        descnode["domain"] = self.get_domain() if self.get_domain() else "cpp"
        descnode += signode
        descnode += contentnode

        return [descnode]

    def visit_Node_docVariableListType(self, node: parser.Node_docVariableListType) -> list[Node]:
        output: list[Node] = []
        for n in node:
            descnode = addnodes.desc()
            descnode["objtype"] = "varentry"
            descnode["domain"] = self.get_domain() if self.get_domain() else "cpp"
            signode = addnodes.desc_signature()
            signode += self.render_optional(n.varlistentry)
            descnode += signode
            contentnode = addnodes.desc_content()
            contentnode += self.render_iterable(n.listitem.para)
            descnode += contentnode
            output.append(descnode)
        return output

    def visit_Node_docVarListEntryType(self, node: parser.Node_docVarListEntryType) -> list[Node]:
        return self.render_iterable(node.term)

    def visit_Node_docAnchorType(self, node: parser.Node_docAnchorType) -> list[Node]:
        return list(self.create_doxygen_target(node))

    def visit_Node_docEntryType(self, node: parser.Node_docEntryType) -> list[Node]:
        col = nodes.entry()
        col += self.render_iterable(node.para)
        if node.thead == "yes":
            col["heading"] = True
        if node.rowspan:
            col["morerows"] = int(node.rowspan) - 1
        if node.colspan:
            col["morecols"] = int(node.colspan) - 1
        return [col]

    def visit_Node_docRowType(self, node: parser.Node_docRowType) -> list[Node]:
        row = nodes.row()
        cols = self.render_iterable(node.entry)
        elem: Union[nodes.thead, nodes.tbody]
        if all(col.get("heading", False) for col in cols):
            elem = nodes.thead()
        else:
            elem = nodes.tbody()
        row += cols
        elem.append(row)
        return [elem]

    def visit_Node_docTableType(self, node: parser.Node_docTableType) -> list[Node]:
        table = nodes.table()
        table["classes"] += ["colwidths-auto"]
        tgroup = nodes.tgroup(cols=node.cols)
        for _ in range(node.cols):
            colspec = nodes.colspec()
            colspec.attributes["colwidth"] = "auto"
            tgroup += colspec
        table += tgroup
        rows = self.render_iterable(node.row)

        # this code depends on visit_Node_docRowType(), and expects the same elements used to
        # "envelop" rows there, namely thead and tbody (eg it will need to be updated
        # if Doxygen one day adds support for tfoot)

        tags: dict[str, list] = {row.starttag(): [] for row in rows}
        for row in rows:
            tags[row.starttag()].append(row.next_node())

        def merge_row_types(root, elem, elems):
            for node in elems:
                elem += node
            root += elem

        for klass in [nodes.thead, nodes.tbody]:
            obj = klass()
            if obj.starttag() in tags:
                merge_row_types(tgroup, obj, tags[obj.starttag()])

        return [table]

    def visit_mixedcontainer(self, node: compoundsuper.MixedContainer) -> list[Node]:
        return self.render_optional(node.getValue())

    visit_Node_descriptionType = render_iterable

    visit_Node_linkedTextType = render_iterable

    def visit_function(self, node) -> list[Node]:
        dom = self.get_domain()
        if not dom or dom in ("c", "cpp", "py", "cs"):
            names = self.get_qualification()
            names.append(node.get_name())
            name = self.join_nested_name(names)
            if dom == "py":
                declaration = name + node.get_argsstring()
            elif dom == "cs":
                declaration = " ".join(
                    [
                        self.create_template_prefix(node),
                        "".join(n.astext() for n in self.render(node.get_type())),
                        name,
                        node.get_argsstring(),
                    ]
                )
            else:
                elements = [self.create_template_prefix(node)]
                if node.static == "yes":
                    elements.append("static")
                if node.inline == "yes":
                    elements.append("inline")
                if node.kind == "friend":
                    elements.append("friend")
                if node.virt in ("virtual", "pure-virtual"):
                    elements.append("virtual")
                if node.explicit == "yes":
                    elements.append("explicit")
                # TODO: handle constexpr when parser has been updated
                #       but Doxygen seems to leave it in the type anyway
                typ = "".join(n.astext() for n in self.render(node.get_type()))
                # Doxygen sometimes leaves 'static' in the type,
                # e.g., for "constexpr static auto f()"
                typ = typ.replace("static ", "")
                # In Doxygen up to somewhere between 1.8.17 to exclusive 1.9.1
                # the 'friend' part is also left in the type.
                # See also #767.
                if typ.startswith("friend "):
                    typ = typ[7:]
                elements.append(typ)
                elements.append(name)
                elements.append(node.get_argsstring())
                declaration = " ".join(elements)
            nodes = self.handle_declaration(node, declaration)
            return nodes
        else:
            # Get full function signature for the domain directive.
            param_list = []
            for param in node.param:
                assert self.context is not None
                param = self.context.mask_factory.mask(param)
                param_decl = get_param_decl(param)
                param_list.append(param_decl)
            templatePrefix = self.create_template_prefix(node)
            signature = "{0}{1}({2})".format(
                templatePrefix, get_definition_without_template_args(node), ", ".join(param_list)
            )

            # Add CV-qualifiers.
            if node.const == "yes":
                signature += " const"
            # The doxygen xml output doesn't register 'volatile' as the xml attribute for functions
            # until version 1.8.8 so we also check argsstring:
            #     https://bugzilla.gnome.org/show_bug.cgi?id=733451
            if node.volatile == "yes" or node.argsstring.endswith("volatile"):
                signature += " volatile"

            if node.refqual == "lvalue":
                signature += "&"
            elif node.refqual == "rvalue":
                signature += "&&"

            # Add `= 0` for pure virtual members.
            if node.virt == "pure-virtual":
                signature += "= 0"

            assert self.context is not None
            self.context.directive_args[1] = [signature]

            nodes = self.run_domain_directive(node.kind, self.context.directive_args[1])

            assert self.app.env is not None
            if self.app.env.config.breathe_debug_trace_doxygen_ids:
                target = self.create_doxygen_target(node)
                if len(target) == 0:
                    print("{}Doxygen target (old): (none)".format("  " * _debug_indent))
                else:
                    print(
                        "{}Doxygen target (old): {}".format("  " * _debug_indent, target[0]["ids"])
                    )

            rst_node = nodes[1]
            finder = NodeFinder(rst_node.document)
            rst_node.walk(finder)

            # Templates have multiple signature nodes in recent versions of Sphinx.
            # Insert Doxygen target into the first signature node.
            if not self.app.env.config.breathe_debug_trace_doxygen_ids:
                target = self.create_doxygen_target(node)
            rst_node.children[0].insert(0, target)

            finder.content.extend(self.description(node))
            return nodes

    def visit_define(self, node) -> list[Node]:
        declaration = node.name
        if node.param:
            declaration += "("
            for i, parameter in enumerate(node.param):
                if i:
                    declaration += ", "
                declaration += parameter.defname
            declaration += ")"

        # TODO: remove this once Sphinx supports definitions for macros
        def add_definition(declarator: Declarator) -> None:
            if node.initializer and self.app.config.breathe_show_define_initializer:
                declarator.append(nodes.Text(" "))
                declarator.extend(self.render(node.initializer))

        return self.handle_declaration(node, declaration, declarator_callback=add_definition)

    def visit_enum(self, node) -> list[Node]:
        def content(contentnode):
            contentnode.extend(self.description(node))
            values = nodes.emphasis("", nodes.Text("Values:"))
            title = nodes.paragraph("", "", values)
            contentnode += title
            enums = self.render_iterable(node.enumvalue)
            contentnode.extend(enums)

        names = self.get_qualification()
        names.append(node.name)
        declaration = self.join_nested_name(names)
        dom = self.get_domain()
        if (not dom or dom == "cpp") and node.strong == "yes":
            # It looks like Doxygen does not make a difference
            # between 'enum class' and 'enum struct',
            # so render them both as 'enum class'.
            obj_type = "enum-class"
            underlying_type = "".join(n.astext() for n in self.render(node.type_))
            if len(underlying_type.strip()) != 0:
                declaration += " : "
                declaration += underlying_type
        else:
            obj_type = "enum"
        return self.handle_declaration(
            node, declaration, obj_type=obj_type, content_callback=content
        )

    def visit_Node_enumvalueType(self, node: parser.Node_enumvalueType) -> list[Node]:
        if self.app.config.breathe_show_enumvalue_initializer:
            declaration = node.name + self.make_initializer(node)
        else:
            declaration = node.name
        return self.handle_declaration(node, declaration, obj_type="enumvalue")

    def visit_typedef(self, node) -> list[Node]:
        type_ = "".join(n.astext() for n in self.render(node.get_type()))
        names = self.get_qualification()
        names.append(node.get_name())
        name = self.join_nested_name(names)
        if node.definition.startswith("using "):
            # TODO: looks like Doxygen does not generate the proper XML
            #       for the template parameter list
            declaration = self.create_template_prefix(node)
            declaration += " " + name + " = " + type_
        else:
            # TODO: Both "using" and "typedef" keywords get into this function,
            #   and if no @typedef comment was added, the definition should
            #   contain the full text. If a @typedef was used instead, the
            #   definition has only the typename, which makes it impossible to
            #   distinguish between them so fallback to "typedef" behavior here.
            declaration = " ".join([type_, name, node.get_argsstring()])
        return self.handle_declaration(node, declaration)

    def make_initializer(self, node) -> str:
        initializer = node.initializer
        signature: list[Node] = []
        if initializer:
            render_nodes = self.render(initializer)
            # Do not append separators for paragraphs.
            if not isinstance(render_nodes[0], nodes.paragraph):
                separator = " "
                assert isinstance(render_nodes[0], nodes.Text)
                if not render_nodes[0].startswith("="):
                    separator += "= "
                signature.append(nodes.Text(separator))
            signature.extend(render_nodes)
        return "".join(n.astext() for n in signature)

    def visit_variable(self, node) -> list[Node]:
        names = self.get_qualification()
        names.append(node.name)
        name = self.join_nested_name(names)
        dom = self.get_domain()
        options = {}
        if dom == "py":
            declaration = name
            initializer = self.make_initializer(node).strip().lstrip("=").strip()
            if len(initializer) != 0:
                options["value"] = initializer
        elif dom == "cs":
            declaration = " ".join(
                [
                    self.create_template_prefix(node),
                    "".join(n.astext() for n in self.render(node.get_type())),
                    name,
                    node.get_argsstring(),
                ]
            )
            if node.get_gettable() or node.get_settable():
                declaration += "{"
                if node.get_gettable():
                    declaration += "get;"
                if node.get_settable():
                    declaration += "set;"
                declaration += "}"
            declaration += self.make_initializer(node)
        else:
            elements = [self.create_template_prefix(node)]
            if node.static == "yes":
                elements.append("static")
            if node.mutable == "yes":
                elements.append("mutable")
            typename = "".join(n.astext() for n in self.render(node.get_type()))
            # Doxygen sometimes leaves 'static' in the type,
            # e.g., for "constexpr static int i"
            typename = typename.replace("static ", "")
            if dom == "c" and "::" in typename:
                typename = typename.replace("::", ".")
            elements.append(typename)
            elements.append(name)
            elements.append(node.get_argsstring())
            elements.append(self.make_initializer(node))
            declaration = " ".join(elements)
        if not dom or dom in ("c", "cpp", "py", "cs"):
            return self.handle_declaration(node, declaration, options=options)
        else:
            return self.render_declaration(node, declaration)

    def visit_friendclass(self, node) -> list[Node]:
        dom = self.get_domain()
        assert not dom or dom == "cpp"

        desc = addnodes.desc()
        desc["objtype"] = "friendclass"
        desc["domain"] = self.get_domain() if self.get_domain() else "cpp"
        signode = addnodes.desc_signature()
        desc += signode

        typ = "".join(n.astext() for n in self.render(node.get_type()))
        # in Doxygen < 1.9 the 'friend' part is there, but afterwards not
        # https://github.com/michaeljones/breathe/issues/616
        assert typ in ("friend class", "friend struct", "class", "struct")
        if not typ.startswith("friend "):
            typ = "friend " + typ
        signode += addnodes.desc_annotation(typ, typ)
        signode += nodes.Text(" ")
        # expr = cpp.CPPExprRole(asCode=False)
        # expr.text = node.name
        # TODO: set most of the things that SphinxRole.__call__ sets
        # signode.extend(expr.run())
        signode += nodes.Text(node.name)
        return [desc]

    def visit_templateparam(
        self, node: parser.Node_paramType, *, insertDeclNameByParsing: bool = False
    ) -> list[Node]:
        nodelist: list[Node] = []

        # Parameter type
        if node.type:
            type_nodes = self.render(node.type)
            # Render keywords as annotations for consistency with the cpp domain.
            if len(type_nodes) > 0 and isinstance(type_nodes[0], str):
                first_node = type_nodes[0]
                for keyword in ["typename", "class"]:
                    if first_node.startswith(keyword + " "):
                        type_nodes[0] = nodes.Text(first_node.replace(keyword, "", 1))
                        type_nodes.insert(0, addnodes.desc_annotation(keyword, keyword))
                        break
            nodelist.extend(type_nodes)

        # Parameter name
        if node.declname:
            dom = self.get_domain()
            if not dom:
                dom = "cpp"
            appendDeclName = True
            if insertDeclNameByParsing:
                if dom == "cpp" and sphinx.version_info >= (4, 1, 0):
                    parser = cpp.DefinitionParser(
                        "".join(n.astext() for n in nodelist),
                        location=self.state.state_machine.get_source_and_line(),
                        config=self.app.config,
                    )
                    try:
                        # we really should use _parse_template_parameter()
                        # but setting a name there is non-trivial, so we use type
                        ast = parser._parse_type(named="single", outer="templateParam")
                        assert ast.name is None
                        nn = cpp.ASTNestedName(
                            names=[
                                cpp.ASTNestedNameElement(cpp.ASTIdentifier(node.declname), None)
                            ],
                            templates=[False],
                            rooted=False,
                        )
                        ast.name = nn
                        # the actual nodes don't matter, as it is astext()-ed later
                        nodelist = [nodes.Text(str(ast))]
                        appendDeclName = False
                    except cpp.DefinitionError:
                        # happens with "typename ...Args", so for now, just append
                        pass

            if appendDeclName:
                if nodelist:
                    nodelist.append(nodes.Text(" "))
                nodelist.append(nodes.emphasis(text=node.declname))
        elif self.output_defname and node.defname:
            # We only want to output the definition name (from the cpp file) if the declaration name
            # (from header file) isn't present
            if nodelist:
                nodelist.append(nodes.Text(" "))
            nodelist.append(nodes.emphasis(text=node.defname))

        # array information
        if node.array:
            nodelist.append(nodes.Text(node.array))

        # Default value
        if node.defval:
            nodelist.append(nodes.Text(" = "))
            nodelist.extend(self.render(node.defval))

        return nodelist

    def visit_Node_templateparamlistType(self, node: parser.Node_templateparamlistType) -> list[Node]:
        nodelist: list[Node] = []
        self.output_defname = False
        for i, item in enumerate(node.param):
            if i:
                nodelist.append(nodes.Text(", "))
            nodelist.extend(self.visit_templateparam(item, insertDeclNameByParsing=True))
        self.output_defname = True
        return nodelist

    def visit_Node_docParamListType(self, node: parser.Node_docParamListType) -> list[Node]:
        """Parameter/Exception/TemplateParameter documentation"""

        fieldListName = {
            "param": "param",
            "exception": "throws",
            "templateparam": "tparam",
            # retval support available on Sphinx >= 4.3
            "retval": "returns" if sphinx.version_info[0:2] < (4, 3) else "retval",
        }

        # https://docutils.sourceforge.io/docs/ref/doctree.html#field-list
        fieldList = nodes.field_list()
        for item in node.parameteritem:
            # TODO: does item.parameternamelist really have more than 1 parametername?
            assert len(item.parameternamelist) <= 1, item.parameternamelist
            nameNodes: list[Node] = []
            parameterDirectionNodes = []
            if len(item.parameternamelist) != 0:
                paramNameNodes = item.parameternamelist[0].parametername
                if len(paramNameNodes) != 0:
                    nameNodes = []
                    for paramName in paramNameNodes:
                        content = paramName.content_
                        # this is really a list of MixedContainer objects, i.e., a generic object
                        # we assume there is either 1 or 2 elements, if there is 2 the first is the
                        # parameter direction
                        assert len(content) == 1 or len(content) == 2, content
                        thisName = self.render(content[-1])
                        if len(nameNodes) != 0:
                            if node.kind == "exception":
                                msg = "Doxygen \\exception commands with multiple names can not be"
                                msg += " converted to a single :throws: field in Sphinx."
                                msg += " Exception '{}' suppresed from output.".format(
                                    "".join(n.astext() for n in thisName)
                                )
                                self.state.document.reporter.warning(msg)
                                continue
                            nameNodes.append(nodes.Text(", "))
                        nameNodes.extend(thisName)
                        if len(content) == 2:
                            # note, each paramName node seems to have the same direction,
                            # so just use the last one
                            dir = "".join(n.astext() for n in self.render(content[0])).strip()
                            assert dir in ("[in]", "[out]", "[inout]"), ">" + dir + "<"
                            parameterDirectionNodes = [nodes.strong(dir, dir), nodes.Text(" ", " ")]
            # it seems that Sphinx expects the name to be a single node,
            # so let's make it that
            txt = fieldListName[node.kind] + " "
            for n in nameNodes:
                txt += n.astext()
            name = nodes.field_name("", nodes.Text(txt))
            bodyNodes = self.render_optional(item.parameterdescription)
            # TODO: is it correct that bodyNodes is either empty or a single paragraph?
            assert len(bodyNodes) <= 1, bodyNodes
            if len(bodyNodes) == 1:
                assert isinstance(bodyNodes[0], nodes.paragraph)
                bodyNodes = [
                    nodes.paragraph("", "", *(parameterDirectionNodes + bodyNodes[0].children))
                ]
            body = nodes.field_body("", *bodyNodes)
            field = nodes.field("", name, body)
            fieldList += field
        return [fieldList]

    def visit_docdot(self, node) -> list[Node]:
        """Translate node from doxygen's dot command to sphinx's graphviz directive."""
        graph_node = graphviz()
        if node.content_ and node.content_[0].getValue().rstrip("\n"):
            graph_node["code"] = node.content_[0].getValue()
        else:
            graph_node["code"] = ""  # triggers another warning from sphinx.ext.graphviz
            self.state.document.reporter.warning(
                # would be better if this output includes the parent node's
                # name/reference, but that would always be a <para> element.
                "no content provided for generating DOT graph."
            )
        graph_node["options"] = {}
        if node.caption:
            caption_node = nodes.caption(node.caption, "")
            caption_node += nodes.Text(node.caption)
            return [nodes.figure("", graph_node, caption_node)]
        return [graph_node]

    def visit_docdotfile(self, node) -> list[Node]:
        """Translate node from doxygen's dotfile command to sphinx's graphviz directive."""
        dotcode = ""
        dot_file_path = node.name  # type: str
        # Doxygen v1.9.3+ uses a relative path to specify the dot file.
        # Previously, Doxygen used an absolute path.
        # This relative path is with respect to the XML_OUTPUT path.
        # Furthermore, Doxygen v1.9.3+ will copy the dot file into the XML_OUTPUT
        if not os.path.isabs(dot_file_path):
            # Use self.project_info.project_path as the XML_OUTPUT path, and
            # make it absolute with consideration to the conf.py path
            project_path = self.project_info.project_path()
            if os.path.isabs(project_path):
                dot_file_path = os.path.abspath(project_path + os.sep + dot_file_path)
            else:
                dot_file_path = os.path.abspath(
                    self.app.confdir + os.sep + project_path + os.sep + dot_file_path
                )
        try:
            with open(dot_file_path, encoding="utf-8") as fp:
                dotcode = fp.read()
            if not dotcode.rstrip("\n"):
                raise RuntimeError("%s found but without any content" % dot_file_path)
        except OSError as exc:
            # doxygen seems to prevent this from triggering as a non-existant file
            # generates no XML output for the corresponding `\dotfile` cmd
            self.state.document.reporter.warning(exc)  # better safe than sorry
        except RuntimeError as exc:
            self.state.document.reporter.warning(exc)
        graph_node = graphviz()
        graph_node["code"] = dotcode
        graph_node["options"] = {"docname": dot_file_path}
        caption = "" if not node.content_ else node.content_[0].getValue()
        if caption:
            caption_node = nodes.caption(caption, "")
            caption_node += nodes.Text(caption)
            return [nodes.figure("", graph_node, caption_node)]
        return [graph_node]

    def visit_Node_graphType(self, node: parser.Node_graphType) -> list[Node]:
        """Create a graph (generated by doxygen - not user-defined) from XML using dot
        syntax."""
        # use graphs' legend from doxygen (v1.9.1)
        # most colors can be changed via `graphviz_dot_args` in conf.py
        edge_colors = {
            # blue (#1414CE) doesn't contrast well in dark mode.
            # "public-inheritance": "1414CE",  # allow user to customize this one
            "private-inheritance": "8B1A1A",  # hardcoded
            "protected-inheritance": "006400",  # hardcoded
            # the following are demonstrated in the doxygen graphs' legend, but
            # these don't show in XML properly (bug?); these keys are fiction.
            "used-internal": "9C35CE",  # should also be dashed
            "template-instantiated-inheritance": "FFA500",  # should also be dashed
        }

        # assemble the dot syntax we'll pass to the graphviz directive
        dot = "digraph {\n"
        dot += '    graph [bgcolor="#00000000"]\n'  # transparent color for graph's bg
        dot += '    node [shape=rectangle style=filled fillcolor="#FFFFFF"'
        dot += " font=Helvetica padding=2]\n"
        dot += '    edge [color="#1414CE"]\n'
        relations = []
        for g_node in node.node:
            dot += '    "%s" [label="%s"' % (g_node.id, g_node.label)
            dot += ' tooltip="%s"' % g_node.label
            if g_node.id == "1":
                # the disabled grey color is used in doxygen to indicate that the URL is
                # not set (for the compound in focus). Setting this here doesn't allow
                # further customization. Maybe remove this since URL is not used?
                #
                dot += ' fillcolor="#BFBFBF"'  # hardcoded
            # URLs from a doxygen refid won't work in sphinx graphviz; we can't convert
            # the refid until all docs are built, and pending references are un-noticed
            # within graphviz directives. Maybe someone wiser will find a way to do it.
            #
            # dot += ' URL="%s"' % g_node.get_link().get_refid()
            dot += "]\n"
            for child_node in g_node.childnode:
                edge = f'    "{g_node.id}"'
                edge += f' -> "{child_node.refid}" ['
                edge += f"dir={node.direction} "
                # edge labels don't appear in XML (bug?); use tooltip in meantime
                edge += 'tooltip="%s"' % child_node.relation.value
                if child_node.relation.value in edge_colors.keys():
                    edge += ' color="#%s"' % edge_colors.get(child_node.relation.value)
                edge += "]\n"
                relations.append(edge)
        for relation in relations:
            dot += relation
        dot += "}"

        # use generated dot syntax to create a graphviz node
        graph_node = graphviz()
        graph_node["code"] = dot
        graph_node["align"] = "center"
        graph_node["options"] = {}
        caption = node.caption
        # if caption is first node in a figure, then everything that follows is
        # considered a caption. Use a paragraph followed by a figure to center the
        # graph. This may have illegible side effects for very large graphs.
        caption_node = nodes.paragraph("", nodes.Text(caption))
        return [caption_node, nodes.figure("", graph_node)]

    def visit_unknown(self, node) -> list[Node]:
        """Visit a node of unknown type."""
        return []

    def visit_Node_CompoundType(self, node: parser.Node_CompoundType) -> list[Node]:
        """Dispatch handling of a compound node to a suitable visit method."""
        if node.kind in [parser.CompoundKind.file, parser.CompoundKind.dir, parser.CompoundKind.page, parser.CompoundKind.example, parser.CompoundKind.group]:
            return self.visit_file(node)
        return self.visit_compound(node)

    def dispatch_Node_memberdefType(self, node: parser.Node_memberdefType) -> list[Node]:
        """Dispatch handling of a memberdef node to a suitable visit method."""
        if node.kind in ("function", "signal", "slot") or (
            node.kind == "friend" and node.argsstring
        ):
            return self.visit_function(node)
        if node.kind == "enum":
            return self.visit_enum(node)
        if node.kind == "typedef":
            return self.visit_typedef(node)
        if node.kind == "variable":
            return self.visit_variable(node)
        if node.kind == "property":
            # Note: visit like variable for now
            return self.visit_variable(node)
        if node.kind == "event":
            # Note: visit like variable for now
            return self.visit_variable(node)
        if node.kind == "define":
            return self.visit_define(node)
        if node.kind == "friend":
            # note, friend functions should be dispatched further up
            return self.visit_friendclass(node)
        return self.render_declaration(node, update_signature=self.update_signature)

    #methods: dict[str, Callable[[SphinxRenderer, Any], list[Node]]] = {
    #    "verbatim": visit_verbatim,
    #    "docdotfile": visit_docdotfile,
    #    "docdot": visit_docdot,
    #}

    def render_string(self, node: str) -> list[Node]:
        # Skip any nodes that are pure whitespace
        # Probably need a better way to do this as currently we're only doing
        # it skip whitespace between higher-level nodes, but this will also
        # skip any pure whitespace entries in actual content nodes
        #
        # We counter that second issue slightly by allowing through single white spaces
        #
        stripped = node.strip()
        if stripped:
            delimiter = None
            if "<linebreak>" in stripped:
                delimiter = "<linebreak>"
            elif "\n" in stripped:
                delimiter = "\n"
            if delimiter:
                # Render lines as paragraphs because RST doesn't have line breaks.
                return [
                    nodes.paragraph("", "", nodes.Text(line.strip()))
                    for line in node.split(delimiter)
                    if line.strip()
                ]
            # importantly, don't strip whitespace as visit_docpara uses it to collapse
            # consecutive nodes.Text and rerender them with this function.
            return [nodes.Text(node)]
        if node == " ":
            return [nodes.Text(node)]
        return []

    def render(self, node, context: Optional[RenderContext] = None) -> list[Node]:
        assert self.context is not None
        if context is None:
            context = self.context.create_child_context(node)
        with WithContext(self, context):
            result: list[Node] = []
            if not self.filter_.allow(self.context.node_stack):
                pass
            elif isinstance(node, str):
                result = self.render_string(node)
            else:
                assert type(node).__name__.startswith('Node_')
                method = getattr(SphinxRenderer, 'visit_'+type(node).__name__, SphinxRenderer.visit_unknown)
                result = method(self, node)
        return result

    def render_optional(self, node) -> list[Node]:
        """Render a node that can be None."""
        return self.render(node) if node else []


def setup(app: Sphinx) -> None:
    app.add_config_value("breathe_debug_trace_directives", False, "")
    app.add_config_value("breathe_debug_trace_doxygen_ids", False, "")
    app.add_config_value("breathe_debug_trace_qualification", False, "")
