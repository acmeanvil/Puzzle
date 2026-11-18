"""What each field in the Properties panel and Puzzle's forms means: one table for
the forms' description box, tooltips, the Advisor and the help's generated Properties
page (ADR 0036, ADR 0083). Plain data, so the help builds without Qt (ADR 0090
package 16)."""

# Field key -> what it means, with its source where it has one. One table for the
# description box, tooltips and the Advisor.
DESCRIPTIONS = {
    "name": "The name the tree and the results list show.",
    "loading": "Each kind of cycle. Fully reversed: the case and its reverse (mean zero). "
    "Zero-based: the case, then nothing (amplitude and mean half of it). Range: between a "
    "range combination's case 1 and case 2. Scale multiplies the stresses; cycles count "
    "per repetition of the duty (Miner's rule sums them).",
    "stress": "How each node's stress tensor becomes one number. Signed von Mises keeps the "
    "sign of the largest principal stress, so compression doesn't count as tension.",
    "mean_stress": "The completely reversed stress as damaging as the cycle's amplitude and "
    "mean (Shigley Sec. 6-12). A compressive mean gets no benefit.",
    "life_model": "Where cycles to failure come from: Shigley's S-N line from the tensile "
    "strength, the material's measured S-N curve, or its strain-life model.",
    "sn_curve": "Which of the material's S-N curves to read (Material builder, Fatigue tab).",
    "design_life": "How many repetitions of the duty the part must last. Damage and the "
    "safety factor are taken at it.",
    "surface": "Surface factor ka = a·Sut^b (Shigley Eq. 6-19, Table 6-2).",
    "size": "Size factor kb (Shigley Eqs. 6-20 to 6-25): rotating rounds, non-rotating "
    "rounds and rectangles by their effective diameter; axial loading has none.",
    "diameter": "The round's diameter, for its size factor.",
    "height": "The rectangle's height h, for de = 0.808√(h·b).",
    "width": "The rectangle's width b, for de = 0.808√(h·b).",
    "reliability": "Reliability factor ke = 1 − 0.08·za (Shigley Eq. 6-29, Table 6-5).",
    "temperature": "Temperature factor kd (Shigley Eq. 6-27, 21 to 538 °C).",
    "misc_factor": "Other effects kf: plating, corrosion, fretting (Shigley Sec. 6-9).",
    "endurance_limit": "A measured rotating-beam endurance limit S'e. Blank: 0.5 Sut, "
    "steels only (Shigley Eq. 6-8).",
    "notch_from": "The fatigue stress-concentration factor, given, or Kf = 1 + q(Kt − 1) with "
    "Neuber's notch sensitivity q (Shigley Eqs. 6-32 to 6-35, steels). Leave it at 1 where the "
    "mesh resolves the notch: its stresses already include Kt.",
    "notch_factor": "Kf, multiplying the alternating stress. A modelled bolt's thread "
    "takes its own, the bolt's thread Kf (ADR 0077).",
    "notch_kt": "The theoretical stress-concentration factor Kt, from charts or a fine model.",
    "notch_radius": "The notch root radius r, for Neuber's √a/√r.",
    "notch_shear": "Neuber's constant for torsion rather than bending and axial loads.",
    "notch_on_mean": "Kf on the mean stress too. Shigley applies it to the alternating "
    "stress only for ductile materials.",
    "method": "How the cases combine.",
    "quantity": "The result compared at each node across the cases.",
    "extreme": "Keep the largest or the smallest over the cases.",
    "source": "The results the equation reads: a study's latest run, or another derived result.",
    "expression": "An expression over the names listed below, evaluated at each node.",
    "unit": "The kind of unit the value has, so it shows in the display units.",
}
