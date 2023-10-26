from __future__ import annotations

import typing as tp

import einops
import jax
import jax.numpy as jnp

from ..manifold.types import TangentSpace


class CubicSpline(tp.NamedTuple):

    """Cubic spline parameterised by basis of the null-space.

    Parameters:
        p: position of the curve start-point
        q: position of the curve end-point
        num_nodes: number of nodes used to represent the curve
        num_edges: number of edges in the representation
        basis: computed basis of the null-space
    """

    p: jax.Array
    q: jax.Array

    num_nodes: int
    num_edges: int

    basis: jax.Array

    @classmethod
    def from_nodes(cls, p: jax.Array, q: jax.Array, num_nodes: int) -> CubicSpline:
        basis = _compute_basis(num_nodes - 1)
        return cls(p=p, q=q, basis=basis, num_nodes=num_nodes, num_edges=(num_nodes - 1))

    def init_params(self) -> jax.Array:
        return jnp.zeros((self.num_nodes, self.p.shape[0]))

    def evaluate(self, t: jax.Array, params: jax.Array) -> TangentSpace[jax.Array]:
        y = _curve(t, params, self)
        y_dot = _curve_t(t, params, self)

        return TangentSpace(point=y, vector=y_dot)

    def __call__(self, t: jax.Array, params: jax.Array) -> TangentSpace[jax.Array]:
        return self.evaluate(t, params)


def _compute_basis(num_edges: int) -> jax.Array:
    """Compute basis of the null space for constrained cubic spline.

    Parameters:
        num_edges: Number of edges to compute basis for.

    Returns:
        basis: Computed basis of the null space.
    """

    t = jnp.linspace(0, 1, num_edges + 1)[1:-1]

    end_points = jnp.zeros((2, 4 * num_edges))
    end_points = end_points.at[0, 0].set(1.0)
    end_points = end_points.at[1, -4:].set(1.0)

    zeroth = jnp.zeros((num_edges - 1, 4 * num_edges))
    for i in range(num_edges - 1):
        si = 4 * i  # start index
        fill = jnp.array([1.0, t[i], t[i] ** 2, t[i] ** 3])
        zeroth = zeroth.at[i, si : (si + 4)].set(fill)
        zeroth = zeroth.at[i, (si + 4) : (si + 8)].set(-fill)

    first = jnp.zeros((num_edges - 1, 4 * num_edges))
    for i in range(num_edges - 1):
        si = 4 * i  # start index
        fill = jnp.array([0.0, 1.0, 2.0 * t[i], 3.0 * t[i] ** 2])
        first = first.at[i, si : (si + 4)].set(fill)
        first = first.at[i, (si + 4) : (si + 8)].set(-fill)

    second = jnp.zeros((num_edges - 1, 4 * num_edges))
    for i in range(num_edges - 1):
        si = 4 * i
        fill = jnp.array([0.0, 0.0, 6.0 * t[i], 2.0])
        second = second.at[i, si : (si + 4)].set(fill)
        second = second.at[i, (si + 4) : (si + 8)].set(-fill)

    constraints = jnp.concatenate((end_points, zeroth, first, second))

    _, s, vt = jnp.linalg.svd(constraints, compute_uv=True, full_matrices=True)
    basis = vt.T[:, s.size :]

    return basis


def _compute_coefficients(basis: jax.Array, params: jax.Array) -> jax.Array:
    """Compute coefficients for the cubic spline.

    Parameters:
        basis: Basis of the null space for the constrained cubic spline.
        params: Parameters of the curve.

    Returns:
        coefficients: Coefficients of the resulting cubic spline.
    """

    x_in_null = jnp.einsum('...nc, ...cd -> ...nd', basis, params)
    coefficients = einops.rearrange(x_in_null, '... (num_nodes degree) ndim -> ... num_nodes degree ndim', degree=4)

    return coefficients


def _eval_straight_line(t: jax.Array, p: jax.Array, q: jax.Array) -> jax.Array:
    """Evaluate straight line connecting p and q.

    Parameters:
        t: Times to evaluate the straight line at.
        p: First boundary point.
        q: Second boundary point.
    """

    return p + (q - p) * jnp.expand_dims(t, 1)


def _eval_polynomials(t: jax.Array, num_edges: int, coeffs: jax.Array, *, degree: int = 4) -> jax.Array:
    """Evaluate polynomials for the cubic spline.

    Parameters:
        t: Times to evaluate the polynomials at.
        num_edges: Number of edges in the cubic spline.
        coeffs: Coefficients of the cubic spline.
        degree: Degree of polynomial to evaluate.

    Returns:
        Resulting polynomial generated by the cubic spline.
    """

    idx = jnp.floor(t * num_edges).clip(0, num_edges - 1).astype(jnp.int32)

    power = einops.rearrange(jnp.arange(0, degree), 'degree -> 1 degree')
    tpow = jnp.power(jnp.expand_dims(t, 1), power)
    coeffs_idx = coeffs[idx]

    return jnp.einsum('td, tdc -> tdc', tpow, coeffs_idx).sum(1)


def _curve(t: jax.Array, params: jax.Array, spline: CubicSpline) -> jax.Array:
    """Evaluate the cubic spline.

    Parameters:
        t: Times at which to evaluate the cubic spline.
        params: Parameters characterising the spline.
        spline: Object holding information about the spline.

    Returns:
        Resulting evaluation of the cubic spline.
    """

    coefficients = _compute_coefficients(spline.basis, params)

    _linear = _eval_straight_line(t, spline.p, spline.q)
    _pertubation = _eval_polynomials(t, spline.num_edges, coefficients)

    return _linear + _pertubation


def _curve_t(t: jax.Array, params: jax.Array, spline: CubicSpline) -> jax.Array:
    """Evaluate the derivative of the cubic spline.

    Parameters:
        t: Times at which to evaluate the cubic spline.
        params: Parameters characterising the spline.
        spline: Object holding information about the spline.

    Returns:
        Resulting derivative of the cubic spline.
    """

    coefficients = _compute_coefficients(spline.basis, params)
    dcoefficients = coefficients[:, 1:, :] * jnp.arange(1, 4).reshape(1, 3, 1)

    _derivative = spline.q - spline.p + _eval_polynomials(t, spline.num_edges, dcoefficients, degree=3)

    return _derivative


def _curve_tt(t: jax.Array, params: jax.Array, spline: CubicSpline) -> jax.Array:
    """Evaluate the second derivative of the cubic spline.

    Parameters:
        t: Times at which to evaluate the cubic spline.
        params: Parameters characterising the spline.
        spline: Object holding information about the spline.

    Returns:
        Resulting second derivative of the cubic spline.
    """

    coefficients = _compute_coefficients(spline.basis, params)
    dcoefficients = coefficients[:, 2:, :] * jnp.arange(1, 4).reshape(1, 2, 1)

    return _eval_polynomials(t, spline.num_edges, dcoefficients, degree=2)