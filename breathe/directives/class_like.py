from __future__ import annotations

from breathe.directives import BaseDirective
from breathe.file_state_cache import MTimeError
from breathe.project import ProjectError
from breathe.renderer.mask import NullMaskFactory
from breathe.renderer.target import create_target_handler

from docutils.parsers.rst.directives import unchanged_required, unchanged, flag

from typing import cast, ClassVar, TYPE_CHECKING

if TYPE_CHECKING:
    import sys
    if sys.version_info >= (3, 11):
        from typing import NotRequired, TypedDict
    else:
        from typing_extensions import NotRequired, TypedDict
    from breathe import renderer
    from docutils.nodes import Node

    DoxClassOptions = TypedDict('DoxClassOptions',{
        'path': str,
        'project': str,
        'members': NotRequired[str],
        'membergroups': str,
        'members-only': NotRequired[None],
        'protected-members': NotRequired[None],
        'private-members': NotRequired[None],
        'undoc-members': NotRequired[None],
        'show': str,
        'outline': NotRequired[None],
        'no-link': NotRequired[None],
        'allow-dot-graphs': NotRequired[None]})
else:
    DoxClassOptions = None


class _DoxygenClassLikeDirective(BaseDirective):
    kind: ClassVar[str]

    required_arguments = 1
    optional_arguments = 0
    final_argument_whitespace = True
    option_spec = {
        "path": unchanged_required,
        "project": unchanged_required,
        "members": unchanged,
        "membergroups": unchanged_required,
        "members-only": flag,
        "protected-members": flag,
        "private-members": flag,
        "undoc-members": flag,
        "show": unchanged_required,
        "outline": flag,
        "no-link": flag,
        "allow-dot-graphs": flag,
    }
    has_content = False

    def run(self) -> list[Node]:
        name = self.arguments[0]
        options = cast(DoxClassOptions,self.options)

        try:
            project_info = self.project_info_factory.create_project_info(options)
        except ProjectError as e:
            warning = self.create_warning(None, kind=self.kind)
            return warning.warn("doxygen{kind}: %s" % e)

        try:
            finder = self.finder_factory.create_finder(project_info)
        except MTimeError as e:
            warning = self.create_warning(None, kind=self.kind)
            return warning.warn("doxygen{kind}: %s" % e)

        finder_filter = self.filter_factory.create_compound_finder_filter(name, self.kind)

        matches: list[list[renderer.TaggedNode]] = []
        finder.filter_(finder_filter, matches)

        if len(matches) == 0:
            warning = self.create_warning(project_info, name=name, kind=self.kind)
            return warning.warn('doxygen{kind}: Cannot find class "{name}" {tail}')

        target_handler = create_target_handler(options, project_info, self.state.document)
        filter_ = self.filter_factory.create_class_filter(name, options)

        mask_factory = NullMaskFactory()
        return self.render(
            matches[0], project_info, filter_, target_handler, mask_factory, self.directive_args
        )


class DoxygenClassDirective(_DoxygenClassLikeDirective):
    kind = "class"


class DoxygenStructDirective(_DoxygenClassLikeDirective):
    kind = "struct"


class DoxygenInterfaceDirective(_DoxygenClassLikeDirective):
    kind = "interface"
