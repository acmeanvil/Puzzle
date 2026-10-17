from puzzle.model import Quantity, Study, SweepParameter, SweepStep
from puzzle.project import content_hashes, load_project, save_project


def test_round_trip_is_lossless(bracket, tmp_path):
    save_project(bracket, tmp_path)
    loaded = load_project(tmp_path)

    assert loaded.name == bracket.name
    assert loaded.objects.keys() == bracket.objects.keys()
    for oid, obj in bracket.objects.items():
        assert loaded.objects[oid] == obj
    assert content_hashes(loaded) == content_hashes(bracket)


def test_one_file_per_object(bracket, tmp_path):
    save_project(bracket, tmp_path)
    assert len(list(tmp_path.glob("*/*.toml"))) == len(bracket.objects)


def test_deleted_objects_lose_their_files(bracket, tmp_path):
    save_project(bracket, tmp_path)
    victim = next(iter(bracket.objects))
    del bracket.objects[victim]
    save_project(bracket, tmp_path)
    assert victim not in load_project(tmp_path).objects


def test_assembly_with_sweep_round_trips(assembly, tmp_path):
    study = next(s for s in assembly.of_type(Study) if s.name == "Assembly static")
    mm = [Quantity(value=v, unit="mm") for v in (1, 2)]
    study.steps.insert(0, SweepStep(parameters=[SweepParameter(name="fillet_r", values=mm)]))

    save_project(assembly, tmp_path)
    loaded = load_project(tmp_path)
    for oid, obj in assembly.objects.items():
        assert loaded.objects[oid] == obj
    assert content_hashes(loaded) == content_hashes(assembly)
