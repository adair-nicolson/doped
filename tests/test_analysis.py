"""
Tests for the `doped.analysis` module, which also implicitly tests most of the
`doped.utils.parsing` module.
"""

import os
import shutil
import unittest
import warnings
from unittest.mock import patch

import numpy as np
from pymatgen.core.structure import Structure
from test_vasp import _potcars_available

from doped.analysis import defect_entry_from_paths, defect_from_structures, defect_name_from_structures
from doped.generation import DefectsGenerator, get_defect_name_from_defect
from doped.utils.parsing import (
    get_defect_site_idxs_and_unrelaxed_structure,
    get_defect_type_and_composition_diff,
    get_outcar,
    get_vasprun,
)


def if_present_rm(path):
    """
    Remove file or directory if it exists.
    """
    if os.path.exists(path):
        if os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)


# TODO: Test reordered case - have we one from before? - Pretty sure this shouldn't be any issue now
# TODO: Test with Adair BiOI data and Xinwei Sb2Se3 data.
# TODO: Test negative corrections warning with our V_Cd^+1, and also with Adair`s V_Bi^+1 and Xinwei`s
#  cases (no warning in those cases 'cause anisotropic)


class DopedParsingTestCase(unittest.TestCase):
    def setUp(self):
        self.module_path = os.path.dirname(os.path.abspath(__file__))
        self.EXAMPLE_DIR = os.path.join(self.module_path, "../examples")
        self.CDTE_EXAMPLE_DIR = os.path.join(self.module_path, "../examples/CdTe")
        self.YTOS_EXAMPLE_DIR = os.path.join(self.module_path, "../examples/YTOS")
        self.CDTE_BULK_DATA_DIR = os.path.join(self.CDTE_EXAMPLE_DIR, "CdTe_bulk/vasp_ncl")
        self.cdte_dielectric = np.array([[9.13, 0, 0], [0.0, 9.13, 0], [0, 0, 9.13]])  # CdTe

        self.ytos_dielectric = [  # from legacy Materials Project
            [40.71948719643814, -9.282128210266565e-14, 1.26076160303219e-14],
            [-9.301652644020242e-14, 40.71948719776858, 4.149879443489052e-14],
            [5.311743673463141e-15, 2.041077680836527e-14, 25.237620491130023],
        ]

    def tearDown(self):
        if_present_rm("bulk_voronoi_nodes.json")

        if os.path.exists(f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/hidden_otcr.gz"):
            shutil.move(
                f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/hidden_otcr.gz",
                f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/OUTCAR.gz",
            )

        if os.path.exists(f"{self.YTOS_EXAMPLE_DIR}/F_O_1/hidden_otcr.gz"):
            shutil.move(
                f"{self.YTOS_EXAMPLE_DIR}/F_O_1/hidden_otcr.gz",
                f"{self.YTOS_EXAMPLE_DIR}/F_O_1/OUTCAR.gz",
            )

        if_present_rm(f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl/another_LOCPOT.gz")
        if_present_rm(f"{self.CDTE_BULK_DATA_DIR}/another_LOCPOT.gz")
        if_present_rm(f"{self.CDTE_BULK_DATA_DIR}/another_OUTCAR.gz")
        if_present_rm(f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl/another_vasprun.xml.gz")
        if_present_rm(f"{self.CDTE_BULK_DATA_DIR}/another_vasprun.xml.gz")

        if os.path.exists(f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl/hidden_lcpt.gz"):
            shutil.move(
                f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl/hidden_lcpt.gz",
                f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl/LOCPOT.gz",
            )

        if_present_rm(f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/LOCPOT.gz")

    def test_auto_charge_determination(self):
        """
        Test that the defect charge is correctly auto-determined.

        Requires potcars to be available.
        """
        if not _potcars_available():
            return
        defect_path = f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl"

        parsed_v_cd_m2 = defect_entry_from_paths(
            defect_path=defect_path,
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=self.cdte_dielectric,
        )

        parsed_v_cd_m2_explicit_charge = defect_entry_from_paths(
            defect_path=defect_path,
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=self.cdte_dielectric,
            charge_state=-2,
        )
        assert parsed_v_cd_m2.get_ediff() == parsed_v_cd_m2_explicit_charge.get_ediff()
        assert parsed_v_cd_m2.charge_state == -2
        assert parsed_v_cd_m2_explicit_charge.charge_state == -2

        # Check that the correct Freysoldt correction is applied
        correct_correction_dict = {
            "freysoldt_charge_correction": 0.7376460317828045,
        }
        for correction_name, correction_energy in correct_correction_dict.items():
            for defect_entry in [parsed_v_cd_m2, parsed_v_cd_m2_explicit_charge]:
                assert np.isclose(
                    defect_entry.corrections[correction_name],
                    correction_energy,
                    atol=1e-3,
                )

        # test warning when specifying the wrong charge:
        with warnings.catch_warnings(record=True) as w:
            parsed_v_cd_m1 = defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=self.cdte_dielectric,
                charge_state=-1,
            )
            assert len(w) == 1
            assert issubclass(w[-1].category, UserWarning)
            assert (
                "Auto-determined defect charge q=-2 does not match specified charge q=-1. Will continue "
                "with specified charge_state, but beware!" in str(w[-1].message)
            )
            assert np.isclose(
                parsed_v_cd_m1.corrections["freysoldt_charge_correction"], 0.26066457692529815
            )

        # test YTOS, has trickier POTCAR symbols with  Y_sv, Ti, S, O
        ytos_F_O_1 = defect_entry_from_paths(
            f"{self.YTOS_EXAMPLE_DIR}/F_O_1",
            f"{self.YTOS_EXAMPLE_DIR}/Bulk",
            self.ytos_dielectric,
            skip_corrections=True,
        )
        assert ytos_F_O_1.charge_state == 1
        assert np.isclose(ytos_F_O_1.get_ediff(), -0.0852, atol=1e-3)  # uncorrected energy

        ytos_F_O_1 = defect_entry_from_paths(  # with corrections this time
            f"{self.YTOS_EXAMPLE_DIR}/F_O_1",
            f"{self.YTOS_EXAMPLE_DIR}/Bulk",
            self.ytos_dielectric,
        )
        assert np.isclose(ytos_F_O_1.get_ediff(), 0.04176070572680146, atol=1e-3)  # corrected energy
        correction_dict = {
            "kumagai_charge_correction": 0.12699488572686776,
        }
        for correction_name, correction_energy in correction_dict.items():
            assert np.isclose(ytos_F_O_1.corrections[correction_name], correction_energy, atol=1e-3)
        # assert auto-determined interstitial site is correct
        assert np.isclose(
            ytos_F_O_1.defect_supercell_site.distance_and_image_from_frac_coords([0, 0, 0])[0],
            0.0,
            atol=1e-2,
        )

    def test_auto_charge_correction_behaviour(self):
        """
        Test skipping of charge corrections and warnings.
        """
        defect_path = f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl"
        fake_aniso_dielectric = [1, 2, 3]

        with warnings.catch_warnings(record=True) as w:
            parsed_v_cd_m2_fake_aniso = defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=fake_aniso_dielectric,
                charge_state=None if _potcars_available() else -2  # to allow testing on GH Actions
                # (otherwise test auto-charge determination if POTCARs available)
            )
            assert len(w) == 1
            assert issubclass(w[-1].category, UserWarning)
            assert (
                f"An anisotropic dielectric constant was supplied, but `OUTCAR` files (needed to compute "
                f"the _anisotropic_ Kumagai eFNV charge correction) were not found in the defect "
                f"(at {defect_path}) & bulk (at {self.CDTE_BULK_DATA_DIR}) folders.\n`LOCPOT` files were "
                f"found in both defect & bulk folders, and so the Freysoldt (FNV) charge correction "
                f"developed for _isotropic_ materials will be applied here, which corresponds to using "
                f"the effective isotropic average of the supplied anisotropic dielectric. This could "
                f"lead to significant errors for very anisotropic systems and/or relatively small "
                f"supercells!" in str(w[-1].message)
            )

        assert np.isclose(
            parsed_v_cd_m2_fake_aniso.get_ediff() - sum(parsed_v_cd_m2_fake_aniso.corrections.values()),
            7.661,
            atol=3e-3,
        )  # uncorrected energy
        assert np.isclose(parsed_v_cd_m2_fake_aniso.get_ediff(), 10.379714081555262, atol=1e-3)

        # test no warnings when skip_corrections is True
        with warnings.catch_warnings(record=True) as w:
            parsed_v_cd_m2_fake_aniso = defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=fake_aniso_dielectric,
                skip_corrections=True,
                charge_state=-2,
            )
            assert len(w) == 0

        assert np.isclose(
            parsed_v_cd_m2_fake_aniso.get_ediff() - sum(parsed_v_cd_m2_fake_aniso.corrections.values()),
            7.661,
            atol=3e-3,
        )  # uncorrected energy
        assert np.isclose(parsed_v_cd_m2_fake_aniso.get_ediff(), 7.661, atol=1e-3)
        assert parsed_v_cd_m2_fake_aniso.corrections == {}

        # test fake anisotropic dielectric with Int_Te_3_2, which has multiple OUTCARs:
        with warnings.catch_warnings(record=True) as w:
            parsed_int_Te_2_fake_aniso = defect_entry_from_paths(
                defect_path=f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl",
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=fake_aniso_dielectric,
                charge_state=None if _potcars_available() else 2  # to allow testing on GH Actions
                # (otherwise test auto-charge determination if POTCARs available)
            )
            assert (
                f"Multiple `OUTCAR` files found in defect directory:"
                f" {self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl. Using"
                f" {self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/OUTCAR.gz to parse core levels and "
                f"compute the Kumagai (eFNV) image charge correction." in str(w[0].message)
            )
            assert (
                f"Estimated error in the Kumagai (eFNV) charge correction for defect "
                f"{parsed_int_Te_2_fake_aniso.name} is 0.157 eV (i.e. which is greater than the "
                f"`error_tolerance`: 0.050 eV). You may want to check the accuracy of the correction "
                f"by plotting the site potential differences (using "
                f"`defect_entry.get_kumagai_correction()` with `plot=True`). Large errors are often due "
                f"to unstable or shallow defect charge states (which can't be accurately modelled with "
                f"the supercell approach). If this error is not acceptable, you may need to use a larger "
                f"supercell for more accurate energies." in str(w[1].message)
            )

        assert np.isclose(
            parsed_int_Te_2_fake_aniso.get_ediff() - sum(parsed_int_Te_2_fake_aniso.corrections.values()),
            -7.105,
            atol=3e-3,
        )  # uncorrected energy
        assert np.isclose(parsed_int_Te_2_fake_aniso.get_ediff(), -4.991240009587045, atol=1e-3)

        # test isotropic dielectric but only OUTCAR present:
        with warnings.catch_warnings(record=True) as w:
            parsed_int_Te_2 = defect_entry_from_paths(
                defect_path=f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl",
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=self.cdte_dielectric,
                charge_state=2,
            )
        assert len(w) == 1  # no charge correction warning with iso dielectric, parsing from OUTCARs,
        # but multiple OUTCARs present -> warning
        assert np.isclose(parsed_int_Te_2.get_ediff(), -6.2009, atol=1e-3)

        # test warning when only OUTCAR present but no core level info (ICORELEVEL != 0)
        shutil.move(
            f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/OUTCAR.gz",
            f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/hidden_otcr.gz",
        )

        with warnings.catch_warnings(record=True) as w:
            parsed_int_Te_2_fake_aniso = self._check_no_icorelevel_warning_int_te(
                fake_aniso_dielectric,
                w,
                1,
                "-> Charge corrections will not be applied for this defect.",
            )
        assert np.isclose(
            parsed_int_Te_2_fake_aniso.get_ediff() - sum(parsed_int_Te_2_fake_aniso.corrections.values()),
            -7.105,
            atol=3e-3,
        )  # uncorrected energy
        assert np.isclose(parsed_int_Te_2_fake_aniso.get_ediff(), -7.105, atol=1e-3)

        # test warning when no core level info in OUTCAR (ICORELEVEL != 0), but LOCPOT
        # files present, but anisotropic dielectric:
        shutil.copyfile(
            f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl/LOCPOT.gz",
            f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/LOCPOT.gz",
        )

        with warnings.catch_warnings(record=True) as w:
            parsed_int_Te_2_fake_aniso = self._check_no_icorelevel_warning_int_te(
                fake_aniso_dielectric,
                w,
                2,
                "`LOCPOT` files were found in both defect & bulk folders, and so the Freysoldt (FNV) "
                "charge correction developed for _isotropic_ materials will be applied here, "
                "which corresponds to using the effective isotropic average of the supplied anisotropic "
                "dielectric. This could lead to significant errors for very anisotropic systems and/or "
                "relatively small supercells!",
            )

        assert np.isclose(
            parsed_int_Te_2_fake_aniso.get_ediff() - sum(parsed_int_Te_2_fake_aniso.corrections.values()),
            -7.105,
            atol=3e-3,
        )  # uncorrected energy
        assert np.isclose(
            parsed_int_Te_2_fake_aniso.get_ediff(), -4.7620, atol=1e-3
        )  # -4.734 with old voronoi frac coords

        if_present_rm(f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/LOCPOT.gz")

        # rename files back to original:
        shutil.move(
            f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/hidden_otcr.gz",
            f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/OUTCAR.gz",
        )

        # test warning when no OUTCAR or LOCPOT file found:
        defect_path = f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl"
        shutil.move(
            f"{defect_path}/LOCPOT.gz",
            f"{defect_path}/hidden_lcpt.gz",
        )
        with warnings.catch_warnings(record=True) as w:
            parsed_v_cd_m2 = defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=self.cdte_dielectric,
                charge_state=None if _potcars_available() else -2  # to allow testing on GH Actions
                # (otherwise test auto-charge determination if POTCARs available)
            )
            assert len(w) == 1
            assert all(issubclass(warning.category, UserWarning) for warning in w)
            assert (
                f"`LOCPOT` or `OUTCAR` files are not present in both the defect (at {defect_path}) and "
                f"bulk (at {self.CDTE_BULK_DATA_DIR}) folders. These are needed to perform the "
                f"finite-size charge corrections. Charge corrections will not be applied for this defect."
                in str(w[0].message)
            )

        assert np.isclose(
            parsed_v_cd_m2.get_ediff() - sum(parsed_v_cd_m2.corrections.values()), 7.661, atol=3e-3
        )  # uncorrected energy
        assert np.isclose(parsed_v_cd_m2.get_ediff(), 7.661, atol=1e-3)
        assert parsed_v_cd_m2.corrections == {}

        # move LOCPOT back to original:
        shutil.move(f"{defect_path}/hidden_lcpt.gz", f"{defect_path}/LOCPOT.gz")

        # test no warning when no OUTCAR or LOCPOT file found, but charge is zero:
        defect_path = f"{self.CDTE_EXAMPLE_DIR}/v_Cd_0/vasp_ncl"  # no LOCPOT/OUTCAR

        with warnings.catch_warnings(record=True) as w:
            parsed_v_cd_0 = defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=self.cdte_dielectric,
                charge_state=None if _potcars_available() else 0  # to allow testing on GH Actions
                # (otherwise test auto-charge determination if POTCARs available)
            )
            assert len(w) == 0

        assert np.isclose(
            parsed_v_cd_0.get_ediff() - sum(parsed_v_cd_0.corrections.values()), 4.166, atol=3e-3
        )  # uncorrected energy
        assert np.isclose(parsed_v_cd_0.get_ediff(), 4.166, atol=1e-3)

    def _check_no_icorelevel_warning_int_te(self, dielectric, warnings, num_warnings, action):
        result = defect_entry_from_paths(
            defect_path=f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl",
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=dielectric,
            charge_state=2,
        )
        assert len(warnings) == num_warnings
        assert all(issubclass(warning.category, UserWarning) for warning in warnings)
        assert (
            f"An anisotropic dielectric constant was supplied, but `OUTCAR` files (needed to compute the "
            f"_anisotropic_ Kumagai eFNV charge correction) in the defect (at "
            f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl) & bulk (at {self.CDTE_BULK_DATA_DIR}) folders "
            f"were unable to be parsed, giving the following error message:\nUnable to parse atomic core "
            f"potentials from defect `OUTCAR` at "
            f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/OUTCAR_no_core_levels.gz. This can happen if "
            f"`ICORELEVEL` was not set to 0 (= default) in the `INCAR`, or if the calculation was "
            f"finished prematurely with a `STOPCAR`. The Kumagai charge correction cannot be computed "
            f"without this data!\n{action}" in str(warnings[0].message)
        )

        return result

    def _parse_Int_Te_3_2_and_count_warnings(self, fake_aniso_dielectric, w, num_warnings):
        defect_entry_from_paths(
            defect_path=f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl",
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=fake_aniso_dielectric,
            charge_state=2,
        )
        assert len(w) == num_warnings
        # defect and bulk)
        assert all(issubclass(warning.category, UserWarning) for warning in w)

    def test_multiple_outcars(self):
        shutil.copyfile(
            f"{self.CDTE_BULK_DATA_DIR}/OUTCAR.gz",
            f"{self.CDTE_BULK_DATA_DIR}/another_OUTCAR.gz",
        )
        fake_aniso_dielectric = [1, 2, 3]
        with warnings.catch_warnings(record=True) as w:
            self._parse_Int_Te_3_2_and_count_warnings(fake_aniso_dielectric, w, 3)
            assert (
                f"Multiple `OUTCAR` files found in bulk directory: {self.CDTE_BULK_DATA_DIR}. Using"
                f" {self.CDTE_BULK_DATA_DIR}/OUTCAR.gz to parse core levels and compute the Kumagai ("
                f"eFNV) image charge correction." in str(w[0].message)
            )
            assert (
                f"Multiple `OUTCAR` files found in defect directory:"
                f" {self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl. Using"
                f" {self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl/OUTCAR.gz to parse core levels and "
                f"compute the Kumagai (eFNV) image charge correction." in str(w[1].message)
            )
            # other warnings is charge correction error warning, already tested

        with warnings.catch_warnings(record=True) as w:
            self._parse_Int_Te_3_2_and_count_warnings(fake_aniso_dielectric, w, 3)

    def test_multiple_locpots(self):
        defect_path = f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl"

        shutil.copyfile(f"{defect_path}/LOCPOT.gz", f"{defect_path}/another_LOCPOT.gz")
        shutil.copyfile(
            f"{self.CDTE_BULK_DATA_DIR}/LOCPOT.gz",
            f"{self.CDTE_BULK_DATA_DIR}/another_LOCPOT.gz",
        )

        with warnings.catch_warnings(record=True) as w:
            defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=self.cdte_dielectric,
                charge_state=-2,
            )
            assert len(w) == 2  # multiple LOCPOTs (both defect and bulk)
            assert all(issubclass(warning.category, UserWarning) for warning in w)
            assert (
                f"Multiple `LOCPOT` files found in bulk directory: {self.CDTE_BULK_DATA_DIR}. Using"
                f" {self.CDTE_BULK_DATA_DIR}/LOCPOT.gz to parse the electrostatic potential and compute "
                f"the Freysoldt (FNV) charge correction." in str(w[0].message)
            )
            assert (
                f"Multiple `LOCPOT` files found in defect directory: {defect_path}. Using"
                f" {defect_path}/LOCPOT.gz to parse the electrostatic potential and compute the "
                f"Freysoldt (FNV) charge correction." in str(w[1].message)
            )

    def test_multiple_vaspruns(self):
        defect_path = f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl"

        shutil.copyfile(f"{defect_path}/vasprun.xml.gz", f"{defect_path}/another_vasprun.xml.gz")
        shutil.copyfile(
            f"{self.CDTE_BULK_DATA_DIR}/vasprun.xml.gz",
            f"{self.CDTE_BULK_DATA_DIR}/another_vasprun.xml.gz",
        )

        with warnings.catch_warnings(record=True) as w:
            defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=self.cdte_dielectric,
                charge_state=None if _potcars_available() else -2  # to allow testing on GH Actions
                # (otherwise test auto-charge determination if POTCARs available)
            )
            assert len(w) == 2  # multiple `vasprun.xml`s (both defect and bulk)
            assert all(issubclass(warning.category, UserWarning) for warning in w)
            assert (
                f"Multiple `vasprun.xml` files found in bulk directory: {self.CDTE_BULK_DATA_DIR}. Using"
                f" {self.CDTE_BULK_DATA_DIR}/vasprun.xml.gz to parse the calculation energy and metadata."
                in str(w[0].message)
            )
            assert (
                f"Multiple `vasprun.xml` files found in defect directory: {defect_path}. Using"
                f" {defect_path}/vasprun.xml.gz to parse the calculation energy and metadata."
                in str(w[1].message)
            )

    def test_dielectric_initialisation(self):
        """
        Test that dielectric can be supplied as float or int or 3x1 array/list
        or 3x3 array/list.
        """
        defect_path = f"{self.CDTE_EXAMPLE_DIR}/v_Cd_-2/vasp_ncl"
        # get correct Freysoldt correction energy:
        parsed_v_cd_m2 = defect_entry_from_paths(  # defect charge determined automatically
            defect_path=defect_path,
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=self.cdte_dielectric,
            charge_state=-2,
        )

        # Check that the correct Freysoldt correction is applied
        correct_correction_dict = {
            "freysoldt_charge_correction": 0.7376460317828045,
        }
        for correction_name, correction_energy in correct_correction_dict.items():
            assert np.isclose(
                parsed_v_cd_m2.corrections[correction_name],
                correction_energy,
                atol=1e-3,
            )

        # test float
        new_parsed_v_cd_m2 = defect_entry_from_paths(
            defect_path=defect_path,
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=9.13,
            charge_state=None if _potcars_available() else -2  # to allow testing on GH Actions
            # (otherwise test auto-charge determination if POTCARs available)
        )
        for correction_name, correction_energy in correct_correction_dict.items():
            assert np.isclose(
                new_parsed_v_cd_m2.corrections[correction_name],
                correction_energy,
                atol=1e-3,
            )

        # test int
        new_parsed_v_cd_m2 = defect_entry_from_paths(
            defect_path=defect_path,
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=9,
            charge_state=-2,
        )
        for correction_name, correction_energy in correct_correction_dict.items():
            assert np.isclose(
                new_parsed_v_cd_m2.corrections[correction_name],
                correction_energy,
                atol=0.1,  # now slightly off because using int()
            )

        # test 3x1 array
        new_parsed_v_cd_m2 = defect_entry_from_paths(
            defect_path=defect_path,
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=np.array([9.13, 9.13, 9.13]),
            charge_state=None if _potcars_available() else -2  # to allow testing on GH Actions
            # (otherwise test auto-charge determination if POTCARs available)
        )
        for correction_name, correction_energy in correct_correction_dict.items():
            assert np.isclose(
                new_parsed_v_cd_m2.corrections[correction_name],
                correction_energy,
                atol=1e-3,
            )

        # test 3x1 list
        new_parsed_v_cd_m2 = defect_entry_from_paths(
            defect_path=defect_path,
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=[9.13, 9.13, 9.13],
            charge_state=-2,
        )
        for correction_name, correction_energy in correct_correction_dict.items():
            assert np.isclose(
                new_parsed_v_cd_m2.corrections[correction_name],
                correction_energy,
                atol=1e-3,
            )

        # test 3x3 array
        new_parsed_v_cd_m2 = defect_entry_from_paths(
            defect_path=defect_path,
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=self.cdte_dielectric,
            charge_state=None if _potcars_available() else -2  # to allow testing on GH Actions
            # (otherwise test auto-charge determination if POTCARs available)
        )
        for correction_name, correction_energy in correct_correction_dict.items():
            assert np.isclose(
                new_parsed_v_cd_m2.corrections[correction_name],
                correction_energy,
                atol=1e-3,
            )

        # test 3x3 list
        new_parsed_v_cd_m2 = defect_entry_from_paths(
            defect_path=defect_path,
            bulk_path=self.CDTE_BULK_DATA_DIR,
            dielectric=self.cdte_dielectric.tolist(),
            charge_state=-2,
        )
        for correction_name, correction_energy in correct_correction_dict.items():
            assert np.isclose(
                new_parsed_v_cd_m2.corrections[correction_name],
                correction_energy,
                atol=1e-3,
            )

    def test_vacancy_parsing_and_freysoldt(self):
        """
        Test parsing of Cd vacancy calculations and correct Freysoldt
        correction calculated.
        """
        parsed_vac_Cd_dict = {}

        for i in os.listdir(self.CDTE_EXAMPLE_DIR):
            if "v_Cd" in i:  # loop folders and parse those with "v_Cd" in name
                defect_path = f"{self.CDTE_EXAMPLE_DIR}/{i}/vasp_ncl"
                defect_charge = int(i[-2:].replace("_", ""))
                # parse with no transformation.json
                parsed_vac_Cd_dict[i] = defect_entry_from_paths(
                    defect_path=defect_path,
                    bulk_path=self.CDTE_BULK_DATA_DIR,
                    dielectric=self.cdte_dielectric,
                    charge_state=None if _potcars_available() else defect_charge  # to allow testing
                    # on GH Actions (otherwise test auto-charge determination if POTCARs available)
                )  # Keep dictionary of parsed defect entries

        assert len(parsed_vac_Cd_dict) == 3
        assert all(f"v_Cd_{i}" in parsed_vac_Cd_dict for i in [0, -1, -2])
        # Check that the correct Freysoldt correction is applied
        for name, energy, correction_dict in [
            (
                "v_Cd_0",
                4.166,
                {},
            ),
            (
                "v_Cd_-1",
                6.355,
                {
                    "freysoldt_charge_correction": 0.22517150393292082,
                },
            ),
            (
                "v_Cd_-2",
                8.398,
                {
                    "freysoldt_charge_correction": 0.7376460317828045,
                },
            ),
        ]:
            assert np.isclose(parsed_vac_Cd_dict[name].get_ediff(), energy, atol=1e-3)
            for correction_name, correction_energy in correction_dict.items():
                assert np.isclose(
                    parsed_vac_Cd_dict[name].corrections[correction_name],
                    correction_energy,
                    atol=1e-3,
                )

            # assert auto-determined vacancy site is correct
            # should be: PeriodicSite: Cd (6.5434, 6.5434, 6.5434) [0.5000, 0.5000, 0.5000]
            if name == "v_Cd_0":
                np.testing.assert_array_almost_equal(
                    parsed_vac_Cd_dict[name].defect_supercell_site.frac_coords, [0.5, 0.5, 0.5]
                )
            else:
                np.testing.assert_array_almost_equal(
                    parsed_vac_Cd_dict[name].defect_supercell_site.frac_coords, [0, 0, 0]
                )

    def test_interstitial_parsing_and_kumagai(self):
        """
        Test parsing of Te (split-)interstitial and Kumagai-Oba (eFNV)
        correction.
        """
        if_present_rm("bulk_voronoi_nodes.json")
        with patch("builtins.print") as mock_print:
            te_i_2_ent = defect_entry_from_paths(
                defect_path=f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl",
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=self.cdte_dielectric,
                charge_state=None if _potcars_available() else +2
                # to allow testing on GH Actions (otherwise test auto-charge determination if POTCARs
                # available)
            )

        mock_print.assert_called_once_with(
            "Saving parsed Voronoi sites (for interstitial site-matching) "
            "to bulk_voronoi_sites.json to speed up future parsing."
        )

        self._check_defect_entry_corrections(te_i_2_ent, -6.2009, 0.9038318161163628)
        # assert auto-determined interstitial site is correct
        # initial position is: PeriodicSite: Te (12.2688, 12.2688, 8.9972) [0.9375, 0.9375, 0.6875]
        np.testing.assert_array_almost_equal(
            te_i_2_ent.defect_supercell_site.frac_coords, [0.834511, 0.943944, 0.69776]
        )

        # run again to check parsing of previous Voronoi sites
        with patch("builtins.print") as mock_print:
            te_i_2_ent = defect_entry_from_paths(
                defect_path=f"{self.CDTE_EXAMPLE_DIR}/Int_Te_3_2/vasp_ncl",
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=self.cdte_dielectric,
                charge_state=+2
                # to allow testing on GH Actions (otherwise test auto-charge determination if POTCARs
                # available)
            )

        mock_print.assert_not_called()
        os.remove("bulk_voronoi_nodes.json")

    def test_substitution_parsing_and_kumagai(self):
        """
        Test parsing of Te_Cd_1 and Kumagai-Oba (eFNV) correction.
        """
        for i in os.listdir(self.CDTE_EXAMPLE_DIR):
            if "Te_Cd" in i:  # loop folders and parse those with "Te_Cd" in name
                defect_path = f"{self.CDTE_EXAMPLE_DIR}/{i}/vasp_ncl"
                defect_charge = int(i[-2:].replace("_", ""))
                # parse with no transformation.json:
                te_cd_1_ent = defect_entry_from_paths(
                    defect_path=defect_path,
                    bulk_path=self.CDTE_BULK_DATA_DIR,
                    dielectric=self.cdte_dielectric,
                    charge_state=defect_charge,
                )

        self._check_defect_entry_corrections(te_cd_1_ent, -2.6676, 0.23840982963691623)
        # assert auto-determined substitution site is correct
        # should be: PeriodicSite: Te (6.5434, 6.5434, 6.5434) [0.5000, 0.5000, 0.5000]
        np.testing.assert_array_almost_equal(
            te_cd_1_ent.defect_supercell_site.frac_coords, [0.475139, 0.475137, 0.524856]
        )

    def test_extrinsic_interstitial_defect_ID(self):
        """
        Test parsing of extrinsic F in YTOS interstitial.
        """
        bulk_sc_structure = Structure.from_file(f"{self.YTOS_EXAMPLE_DIR}/Bulk/POSCAR")
        initial_defect_structure = Structure.from_file(f"{self.YTOS_EXAMPLE_DIR}/Int_F_-1/Relaxed_CONTCAR")
        (def_type, comp_diff) = get_defect_type_and_composition_diff(
            bulk_sc_structure, initial_defect_structure
        )
        assert def_type == "interstitial"
        assert comp_diff == {"F": 1}
        (
            bulk_site_idx,
            defect_site_idx,
            unrelaxed_defect_structure,
        ) = get_defect_site_idxs_and_unrelaxed_structure(
            bulk_sc_structure, initial_defect_structure, def_type, comp_diff
        )
        assert bulk_site_idx is None
        assert defect_site_idx == len(unrelaxed_defect_structure) - 1

        # assert auto-determined interstitial site is correct
        assert np.isclose(
            unrelaxed_defect_structure[defect_site_idx].distance_and_image_from_frac_coords(
                [-0.0005726049122470, -0.0001544430438804, 0.47800736578014720]
            )[0],
            0.0,
            atol=1e-2,
        )  # approx match, not exact because relaxed bulk supercell

    def test_extrinsic_substitution_defect_ID(self):
        """
        Test parsing of extrinsic U_on_Cd in CdTe.
        """
        bulk_sc_structure = Structure.from_file(
            f"{self.CDTE_EXAMPLE_DIR}/CdTe_bulk/CdTe_bulk_supercell_POSCAR"
        )
        initial_defect_structure = Structure.from_file(f"{self.CDTE_EXAMPLE_DIR}/U_on_Cd_POSCAR")
        (
            def_type,
            comp_diff,
        ) = get_defect_type_and_composition_diff(bulk_sc_structure, initial_defect_structure)
        assert def_type == "substitution"
        assert comp_diff == {"Cd": -1, "U": 1}
        (
            bulk_site_idx,
            defect_site_idx,
            unrelaxed_defect_structure,
        ) = get_defect_site_idxs_and_unrelaxed_structure(
            bulk_sc_structure, initial_defect_structure, def_type, comp_diff
        )
        assert bulk_site_idx == 0
        assert defect_site_idx == 63  # last site in structure

        # assert auto-determined substitution site is correct
        np.testing.assert_array_almost_equal(
            unrelaxed_defect_structure[defect_site_idx].frac_coords,
            [0.00, 0.00, 0.00],
            decimal=2,  # exact match because perfect supercell
        )

    def test_extrinsic_interstitial_parsing_and_kumagai(self):
        """
        Test parsing of extrinsic F in YTOS interstitial and Kumagai-Oba (eFNV)
        correction.
        """
        defect_path = f"{self.YTOS_EXAMPLE_DIR}/Int_F_-1/"

        # parse with no transformation.json or explicitly-set-charge:
        with warnings.catch_warnings(record=True) as w:
            int_F_minus1_ent = defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=f"{self.YTOS_EXAMPLE_DIR}/Bulk/",
                dielectric=self.ytos_dielectric,
                charge_state=None if _potcars_available() else -1  # to allow testing
                # on GH Actions (otherwise test auto-charge determination if POTCARs available)
            )
        assert not [warning for warning in w if isinstance(warning.category, UserWarning)]

        correction_dict = self._check_defect_entry_corrections(
            int_F_minus1_ent, 0.7478967131628451, -0.0036182568370900017
        )
        # assert auto-determined interstitial site is correct
        assert np.isclose(
            int_F_minus1_ent.defect_supercell_site.distance_and_image_from_frac_coords(
                [-0.0005726049122470, -0.0001544430438804, 0.4780073657801472]
            )[
                0
            ],  # relaxed site
            0.0,
            atol=1e-2,
        )  # approx match, not exact because relaxed bulk supercell

        os.remove("bulk_voronoi_nodes.json")

        # test error_tolerance setting:
        with warnings.catch_warnings(record=True) as w:
            int_F_minus1_ent = defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=f"{self.YTOS_EXAMPLE_DIR}/Bulk/",
                dielectric=self.ytos_dielectric,
                charge_state=None if _potcars_available() else -1,  # to allow testing
                # on GH Actions (otherwise test auto-charge determination if POTCARs available)
                error_tolerance=0.001,
            )
        assert (
            f"Estimated error in the Kumagai (eFNV) charge correction for defect "
            f"{int_F_minus1_ent.name} is 0.003 eV (i.e. which is greater than the `error_tolerance`: "
            f"0.001 eV). You may want to check the accuracy of the correction by plotting the site "
            f"potential differences (using `defect_entry.get_kumagai_correction()` with "
            f"`plot=True`). Large errors are often due to unstable or shallow defect charge states ("
            f"which can't be accurately modelled with the supercell approach). If this error is not "
            f"acceptable, you may need to use a larger supercell for more accurate energies."
            in str(w[0].message)
        )

        with warnings.catch_warnings(record=True) as w:
            int_F_minus1_ent.get_kumagai_correction()  # default error tolerance, no warning
        assert not [warning for warning in w if isinstance(warning.category, UserWarning)]

        with warnings.catch_warnings(record=True) as w:
            int_F_minus1_ent.get_kumagai_correction(error_tolerance=0.001)
        assert "Estimated error in the Kumagai (eFNV)" in str(w[0].message)

        # test returning correction error:
        corr, corr_error = int_F_minus1_ent.get_kumagai_correction(return_correction_error=True)
        assert np.isclose(corr.correction_energy, correction_dict["kumagai_charge_correction"], atol=1e-3)
        assert np.isclose(corr_error, 0.003, atol=1e-3)

        # test returning correction error with plot:
        corr, fig, corr_error = int_F_minus1_ent.get_kumagai_correction(
            return_correction_error=True, plot=True
        )
        assert np.isclose(corr.correction_energy, correction_dict["kumagai_charge_correction"], atol=1e-3)
        assert np.isclose(corr_error, 0.003, atol=1e-3)

        # test just correction returned with plot = False and return_correction_error = False:
        corr = int_F_minus1_ent.get_kumagai_correction()
        assert np.isclose(corr.correction_energy, correction_dict["kumagai_charge_correction"], atol=1e-3)

    def _check_defect_entry_corrections(self, defect_entry, ediff, correction):
        assert np.isclose(defect_entry.get_ediff(), ediff, atol=0.001)
        assert np.isclose(
            defect_entry.get_ediff() - sum(defect_entry.corrections.values()),
            ediff - correction,
            atol=0.003,
        )
        correction_dict = {"kumagai_charge_correction": correction}
        for correction_name, correction_energy in correction_dict.items():
            assert np.isclose(defect_entry.corrections[correction_name], correction_energy, atol=0.001)
        return correction_dict

    def test_extrinsic_substitution_parsing_and_freysoldt_and_kumagai(self):
        """
        Test parsing of extrinsic F-on-O substitution in YTOS, w/Kumagai-Oba
        (eFNV) and Freysoldt (FNV) corrections.
        """
        # first using Freysoldt (FNV) correction
        defect_path = f"{self.YTOS_EXAMPLE_DIR}/F_O_1/"
        # hide OUTCAR file:
        shutil.move(f"{defect_path}/OUTCAR.gz", f"{defect_path}/hidden_otcr.gz")

        # parse with no transformation.json or explicitly-set-charge:
        with warnings.catch_warnings(record=True) as w:
            F_O_1_ent = defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=f"{self.YTOS_EXAMPLE_DIR}/Bulk/",
                dielectric=self.ytos_dielectric,
                charge_state=None if _potcars_available() else 1  # to allow testing
                # on GH Actions (otherwise test auto-charge determination if POTCARs available)
            )  # check no correction error warning with default tolerance:
        assert not [warning for warning in w if isinstance(warning.category, UserWarning)]

        # test error_tolerance setting:
        with warnings.catch_warnings(record=True) as w:
            F_O_1_ent = defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=f"{self.YTOS_EXAMPLE_DIR}/Bulk/",
                dielectric=self.ytos_dielectric,
                charge_state=None if _potcars_available() else 1,  # to allow testing
                # on GH Actions (otherwise test auto-charge determination if POTCARs available)
                error_tolerance=0.00001,
            )  # check no correction error warning with default tolerance:

        assert any(
            f"Estimated error in the Freysoldt (FNV) charge correction for defect {F_O_1_ent.name} is "
            f"0.000 eV (i.e. which is greater than the `error_tolerance`: 0.000 eV). You may want to "
            f"check the accuracy of the correction by plotting the site potential differences (using "
            f"`defect_entry.get_freysoldt_correction()` with `plot=True`). Large errors are often due to "
            f"unstable or shallow defect charge states (which can't be accurately modelled with the "
            f"supercell approach). If this error is not acceptable, you may need to use a larger "
            f"supercell for more accurate energies." in str(warning.message)
            for warning in w
        )

        with warnings.catch_warnings(record=True) as w:
            F_O_1_ent.get_freysoldt_correction()  # default error tolerance, no warning
        assert not [warning for warning in w if isinstance(warning.category, UserWarning)]

        with warnings.catch_warnings(record=True) as w:
            F_O_1_ent.get_freysoldt_correction(error_tolerance=0.00001)
        assert "Estimated error in the Freysoldt (FNV)" in str(w[0].message)

        # test returning correction error:
        corr, corr_error = F_O_1_ent.get_freysoldt_correction(return_correction_error=True)
        assert np.isclose(corr.correction_energy, 0.11670254204631794, atol=1e-3)
        assert np.isclose(corr_error, 0.000, atol=1e-3)

        # test returning correction error with plot:
        corr, fig, corr_error = F_O_1_ent.get_freysoldt_correction(return_correction_error=True, plot=True)
        assert np.isclose(corr.correction_energy, 0.11670254204631794, atol=1e-3)
        assert np.isclose(corr_error, 0.000, atol=1e-3)

        # test just correction returned with plot = False and return_correction_error = False:
        corr = F_O_1_ent.get_freysoldt_correction()
        assert np.isclose(corr.correction_energy, 0.11670254204631794, atol=1e-3)

        # move OUTCAR file back to original:
        shutil.move(f"{defect_path}/hidden_otcr.gz", f"{defect_path}/OUTCAR.gz")

        self._test_F_O_1_ent(
            F_O_1_ent,
            0.03146836204627482,
            "freysoldt_charge_correction",
            0.11670254204631794,
        )
        # now using Kumagai-Oba (eFNV) correction
        defect_path = f"{self.YTOS_EXAMPLE_DIR}/F_O_1/"
        # parse with no transformation.json or explicitly-set-charge:
        F_O_1_ent = defect_entry_from_paths(
            defect_path=defect_path,
            bulk_path=f"{self.YTOS_EXAMPLE_DIR}/Bulk/",
            dielectric=self.ytos_dielectric,
            charge_state=1,
        )

        self._test_F_O_1_ent(F_O_1_ent, 0.04176, "kumagai_charge_correction", 0.12699488572686776)

    def _test_F_O_1_ent(self, F_O_1_ent, ediff, correction_name, correction):
        assert np.isclose(F_O_1_ent.get_ediff(), ediff, atol=1e-3)
        correction_test_dict = {correction_name: correction}
        for correction_name, correction_energy in correction_test_dict.items():
            assert np.isclose(F_O_1_ent.corrections[correction_name], correction_energy, atol=1e-3)
        # assert auto-determined interstitial site is correct
        assert np.isclose(
            F_O_1_ent.defect_supercell_site.distance_and_image_from_frac_coords([0, 0, 0])[0],
            0.0,
            atol=1e-2,
        )

        return correction_test_dict

    def test_voronoi_structure_mismatch_and_reparse(self):
        """
        Test that a mismatch in bulk_supercell structure from previously parsed
        Voronoi nodes json file with current defect bulk supercell is detected
        and re-parsed.
        """
        with patch("builtins.print") as mock_print:
            for i in os.listdir(self.CDTE_EXAMPLE_DIR):
                if "Int_Te" in i:  # loop folders and parse those with "Int_Te" in name
                    defect_path = f"{self.CDTE_EXAMPLE_DIR}/{i}/vasp_ncl"
                    # parse with no transformation.json or explicitly-set-charge:
                    defect_entry_from_paths(
                        defect_path=defect_path,
                        bulk_path=self.CDTE_BULK_DATA_DIR,
                        dielectric=self.cdte_dielectric,
                        charge_state=None if _potcars_available() else 2  # to allow testing
                        # on GH Actions (otherwise test auto-charge determination if POTCARs available)
                    )

        mock_print.assert_called_once_with(
            "Saving parsed Voronoi sites (for interstitial site-matching) "
            "to bulk_voronoi_sites.json to speed up future parsing."
        )

        with warnings.catch_warnings(record=True) as w:
            defect_path = f"{self.YTOS_EXAMPLE_DIR}/Int_F_-1/"
            # parse with no transformation.json or explicitly-set-charge:
            defect_entry_from_paths(
                defect_path=defect_path,
                bulk_path=f"{self.YTOS_EXAMPLE_DIR}/Bulk/",
                dielectric=self.ytos_dielectric,
                charge_state=None if _potcars_available() else -1  # to allow testing on GH Actions
                # (otherwise test auto-charge determination if POTCARs available)
            )

        warning_message = (
            "Previous bulk_voronoi_nodes.json detected, but does not "
            "match current bulk supercell. Recalculating Voronoi nodes."
        )
        user_warnings = [warning for warning in w if warning.category == UserWarning]
        assert len(user_warnings) == 1
        assert warning_message in str(user_warnings[0].message)
        os.remove("bulk_voronoi_nodes.json")

    def test_tricky_relaxed_interstitial_corrections_kumagai(self):
        """
        Test the eFNV correction performance with tricky-to-locate relaxed
        interstitial sites (Te_i^+1 ground-state and metastable from Kavanagh
        et al.

        2022 doi.org/10.1039/D2FD00043A).
        """
        from pydefect.analyzer.calc_results import CalcResults
        from pydefect.cli.vasp.make_efnv_correction import make_efnv_correction

        def _make_calc_results(directory) -> CalcResults:
            vasprun = get_vasprun(f"{directory}/vasprun.xml.gz")
            outcar = get_outcar(f"{directory}/OUTCAR.gz")
            return CalcResults(
                structure=vasprun.final_structure,
                energy=outcar.final_energy,
                magnetization=outcar.total_mag or 0.0,
                potentials=[-p for p in outcar.electrostatic_potential],
                electronic_conv=vasprun.converged_electronic,
                ionic_conv=vasprun.converged_ionic,
            )

        bulk_calc_results = _make_calc_results(f"{self.CDTE_BULK_DATA_DIR}")

        for name, correction_energy in [
            ("Int_Te_3_Unperturbed_1", 0.2974374231312522),
            ("Int_Te_3_1", 0.3001740745077274),
        ]:
            print("Testing", name)
            defect_calc_results = _make_calc_results(f"{self.CDTE_EXAMPLE_DIR}/{name}/vasp_ncl")
            raw_efnv = make_efnv_correction(
                +1, defect_calc_results, bulk_calc_results, self.cdte_dielectric
            )

            Te_i_ent = defect_entry_from_paths(
                defect_path=f"{self.CDTE_EXAMPLE_DIR}/{name}/vasp_ncl",
                bulk_path=self.CDTE_BULK_DATA_DIR,
                dielectric=9.13,
                charge_state=None if _potcars_available() else +1,  # to allow testing on GH Actions
            )

            efnv_w_doped_site = make_efnv_correction(
                +1,
                defect_calc_results,
                bulk_calc_results,
                self.cdte_dielectric,
                defect_coords=Te_i_ent.sc_defect_frac_coords,
            )

            assert np.isclose(raw_efnv.correction_energy, efnv_w_doped_site.correction_energy, atol=1e-3)
            assert np.isclose(raw_efnv.correction_energy, sum(Te_i_ent.corrections.values()), atol=1e-3)
            assert np.isclose(raw_efnv.correction_energy, correction_energy, atol=1e-3)

            efnv_w_fcked_site = make_efnv_correction(
                +1,
                defect_calc_results,
                bulk_calc_results,
                self.cdte_dielectric,
                defect_coords=Te_i_ent.sc_defect_frac_coords + 0.1,  # shifting to wrong defect site
                # affects correction as expected (~0.02 eV = 7% in this case)
            )
            assert not np.isclose(efnv_w_fcked_site.correction_energy, correction_energy, atol=1e-3)
            assert np.isclose(efnv_w_fcked_site.correction_energy, correction_energy, atol=1e-1)


class DopedParsingFunctionsTestCase(unittest.TestCase):
    def setUp(self):
        self.data_dir = os.path.join(os.path.dirname(__file__), "data")
        self.example_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
        self.prim_cdte = Structure.from_file(f"{self.example_dir}/CdTe/relaxed_primitive_POSCAR")
        self.ytos_bulk_supercell = Structure.from_file(f"{self.example_dir}/YTOS/Bulk/POSCAR")
        self.lmno_primitive = Structure.from_file(f"{self.data_dir}/Li2Mn3NiO8_POSCAR")
        self.non_diagonal_ZnS = Structure.from_file(f"{self.data_dir}/non_diagonal_ZnS_supercell_POSCAR")

        # TODO: Try rattling the structures (and modifying symprec a little to test tolerance?)

    def test_defect_name_from_structures(self):
        # by proxy also tests defect_from_structures
        for struct in [
            self.prim_cdte,
            self.ytos_bulk_supercell,
            self.lmno_primitive,
            self.non_diagonal_ZnS,
        ]:
            defect_gen = DefectsGenerator(struct)
            for defect_entry in [entry for entry in defect_gen.values() if entry.charge_state == 0]:
                print(
                    defect_from_structures(defect_entry.bulk_supercell, defect_entry.defect_supercell),
                    defect_entry.defect_supercell_site,
                )
                assert defect_name_from_structures(
                    defect_entry.bulk_supercell, defect_entry.defect_supercell
                ) == get_defect_name_from_defect(defect_entry.defect)

                # Can't use defect.structure/defect.defect_structure because might be vacancy in a 1/2
                # atom cell etc.:
                # assert defect_name_from_structures(
                #     defect_entry.defect.structure, defect_entry.defect.defect_structure
                # ) == get_defect_name_from_defect(defect_entry.defect)

    def tearDown(self):
        if_present_rm("bulk_voronoi_nodes.json")


class ReorderedParsingTestCase(unittest.TestCase):
    """
    Test cases where the atoms bulk and defect supercells have been reordered
    with respect to each other, but that site-matching and charge corrections
    are still correctly performed.
    """

    def setUp(self):
        self.module_path = os.path.dirname(os.path.abspath(__file__))
        self.cdte_corrections_dir = os.path.join(self.module_path, "data/CdTe_charge_correction_tests")
        self.v_Cd_m2_path = f"{self.cdte_corrections_dir}/v_Cd_-2_vasp_gam"
        self.cdte_dielectric = np.array([[9.13, 0, 0], [0.0, 9.13, 0], [0, 0, 9.13]])  # CdTe

    def test_parsing_cdte(self):
        """
        Test parsing CdTe bulk vasp_gam example.
        """
        parsed_v_cd_m2 = defect_entry_from_paths(
            defect_path=self.v_Cd_m2_path,
            bulk_path=f"{self.cdte_corrections_dir}/bulk_vasp_gam",
            dielectric=self.cdte_dielectric,
            charge_state=-2,
        )
        uncorrected_energy = 7.4475896
        assert np.isclose(
            parsed_v_cd_m2.get_ediff() - sum(parsed_v_cd_m2.corrections.values()),
            uncorrected_energy,
            atol=1e-3,
        )

    def test_kumagai_order(self):
        """
        Test Kumagai defect correction parser can handle mismatched atomic
        orders.
        """
        parsed_v_cd_m2_orig = defect_entry_from_paths(
            defect_path=self.v_Cd_m2_path,
            bulk_path=f"{self.cdte_corrections_dir}/bulk_vasp_gam",
            dielectric=self.cdte_dielectric,
            charge_state=-2,
        )
        parsed_v_cd_m2_alt = defect_entry_from_paths(
            defect_path=self.v_Cd_m2_path,
            bulk_path=f"{self.cdte_corrections_dir}/bulk_vasp_gam_alt",
            dielectric=self.cdte_dielectric,
            charge_state=-2,
        )
        # should use Kumagai correction by default when OUTCARs available
        assert np.isclose(parsed_v_cd_m2_orig.get_ediff(), parsed_v_cd_m2_alt.get_ediff())
        assert np.isclose(
            sum(parsed_v_cd_m2_orig.corrections.values()), sum(parsed_v_cd_m2_alt.corrections.values())
        )

        # test where the ordering is all over the shop; v_Cd_-2 POSCAR with a Te atom, then 31 randomly
        # ordered Cd atoms, then 31 randomly ordered Te atoms:
        parsed_v_cd_m2_alt2 = defect_entry_from_paths(
            defect_path=f"{self.cdte_corrections_dir}/v_Cd_-2_choppy_changy_vasp_gam",
            bulk_path=f"{self.cdte_corrections_dir}/bulk_vasp_gam_alt",
            dielectric=self.cdte_dielectric,
            charge_state=-2,
        )
        # should use Kumagai correction by default when OUTCARs available
        assert np.isclose(parsed_v_cd_m2_orig.get_ediff(), parsed_v_cd_m2_alt2.get_ediff())
        assert np.isclose(
            sum(parsed_v_cd_m2_orig.corrections.values()), sum(parsed_v_cd_m2_alt2.corrections.values())
        )

    def test_freysoldt_order(self):
        """
        Test Freysoldt defect correction parser can handle mismatched atomic
        orders.
        """
        shutil.move(f"{self.v_Cd_m2_path}/OUTCAR.gz", f"{self.v_Cd_m2_path}/hidden_otcr.gz")  # use FNV
        parsed_v_cd_m2_orig = defect_entry_from_paths(
            defect_path=self.v_Cd_m2_path,
            bulk_path=f"{self.cdte_corrections_dir}/bulk_vasp_gam",
            dielectric=self.cdte_dielectric,
            charge_state=-2,
        )
        parsed_v_cd_m2_alt = defect_entry_from_paths(
            defect_path=self.v_Cd_m2_path,
            bulk_path=f"{self.cdte_corrections_dir}/bulk_vasp_gam_alt",
            dielectric=self.cdte_dielectric,
            charge_state=-2,
        )
        shutil.move(f"{self.v_Cd_m2_path}/hidden_otcr.gz", f"{self.v_Cd_m2_path}/OUTCAR.gz")  # move back

        # should use Freysoldt correction by default when OUTCARs not available
        assert np.isclose(parsed_v_cd_m2_orig.get_ediff(), parsed_v_cd_m2_alt.get_ediff())
        assert np.isclose(
            sum(parsed_v_cd_m2_orig.corrections.values()), sum(parsed_v_cd_m2_alt.corrections.values())
        )

        # test where the ordering is all over the shop; v_Cd_-2 POSCAR with a Te atom, then 31 randomly
        # ordered Cd atoms, then 31 randomly ordered Te atoms:
        shutil.move(
            f"{self.cdte_corrections_dir}/v_Cd_-2_choppy_changy_vasp_gam/OUTCAR.gz",
            f"{self.cdte_corrections_dir}/v_Cd_-2_choppy_changy_vasp_gam/hidden_otcr.gz",
        )  # use FNV
        parsed_v_cd_m2_alt2 = defect_entry_from_paths(
            defect_path=f"{self.cdte_corrections_dir}/v_Cd_-2_choppy_changy_vasp_gam",
            bulk_path=f"{self.cdte_corrections_dir}/bulk_vasp_gam",
            dielectric=self.cdte_dielectric,
            charge_state=-2,
        )
        shutil.move(
            f"{self.cdte_corrections_dir}/v_Cd_-2_choppy_changy_vasp_gam/hidden_otcr.gz",
            f"{self.cdte_corrections_dir}/v_Cd_-2_choppy_changy_vasp_gam/OUTCAR.gz",
        )  # move back

        # should use Freysoldt correction by default when OUTCARs not available
        assert np.isclose(parsed_v_cd_m2_orig.get_ediff(), parsed_v_cd_m2_alt2.get_ediff())
        assert np.isclose(
            sum(parsed_v_cd_m2_orig.corrections.values()), sum(parsed_v_cd_m2_alt2.corrections.values())
        )


if __name__ == "__main__":
    unittest.main()
