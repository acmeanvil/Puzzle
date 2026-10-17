"""Geometry: STEP import through Gmsh's OpenCASCADE kernel, named entities, selections.

OCP and build123d (ADR 0004) join when geometry creation and persistent naming
across edits need direct kernel access.
"""

from puzzle.geometry.model import ENTITY_DIM, Entity, GeometryModel, import_step
from puzzle.geometry.session import gmsh_session

__all__ = ["ENTITY_DIM", "Entity", "GeometryModel", "gmsh_session", "import_step"]
