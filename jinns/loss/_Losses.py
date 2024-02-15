"""
Interface for diverse loss functions to factorize code
"""

import jax
import jax.numpy as jnp
from jax import vmap

from jinns.utils._pinn import PINN
from jinns.utils._spinn import SPINN
from jinns.loss._boundary_conditions import (
    _compute_boundary_loss,
)
from jinns.utils._utils import _check_user_func_return, _get_grid


def dynamic_loss_apply(dyn_loss, u, batches, params, vmap_axes, u_type=None):
    if u_type == PINN or isinstance(u, PINN):
        v_dyn_loss = vmap(
            lambda *args: dyn_loss(
                *args[:-1], u, args[-1]  # we must place the params at the end
            ),
            vmap_axes,
            0,
        )
        residuals = v_dyn_loss(*batches, params)
        mse_dyn_loss = jnp.mean(jnp.sum(residuals**2, axis=-1))
    elif u_type == SPINN or isinstance(u, SPINN):
        residuals = dyn_loss(*batches, u, params)
        mse_dyn_loss = jnp.mean(jnp.sum(residuals**2, axis=-1))

    return mse_dyn_loss


def normalization_loss_apply(u, batches, params, vmap_axes, int_length):
    # TODO merge stationary and non stationary cases
    if isinstance(u, PINN):
        if len(batches) == 1:
            v_u = vmap(
                lambda *args: u(*args)[u.slice_solution],
                vmap_axes,
                0,
            )
            mse_norm_loss = jnp.mean(
                jnp.abs(jnp.mean(v_u(*batches, params), axis=-1) * int_length - 1) ** 2
            )
        else:
            v_u = vmap(
                vmap(
                    lambda t, x, params_: u(t, x, params_),
                    in_axes=(None, 0) + vmap_axes[2:],
                ),
                in_axes=(0, None) + vmap_axes[2:],
            )
            res = v_u(*batches, params)
            # the outer mean() below is for the times stamps
            mse_norm_loss = jnp.mean(
                jnp.abs(jnp.mean(res, axis=(-2, -1)) * int_length - 1) ** 2
            )
    elif isinstance(u, SPINN):
        if len(batches) == 1:
            res = u(*batches, params)
            mse_norm_loss = (
                jnp.abs(
                    jnp.mean(
                        jnp.mean(res, axis=-1),
                        axis=tuple(range(res.ndim - 1)),
                    )
                    * int_length
                    - 1
                )
                ** 2
            )
        else:
            assert batches[1].shape[0] % batches[0].shape[0] == 0
            rep_t = batches[1].shape[0] // batches[0].shape[0]
            res = u(jnp.repeat(batches[0], rep_t, axis=0), batches[1], params)
            # the outer mean() below is for the times stamps
            mse_norm_loss = jnp.mean(
                jnp.abs(
                    jnp.mean(
                        jnp.mean(res, axis=-1),
                        axis=(d + 1 for d in range(res.ndim - 2)),
                    )
                    * int_length
                    - 1
                )
                ** 2
            )

    return mse_norm_loss


def boundary_condition_apply(
    u, batch, params, omega_boundary_fun, omega_boundary_condition, omega_boundary_dim
):
    if isinstance(omega_boundary_fun, dict):
        # We must create the facet tree dictionary as we do not have the
        # enumerate from the for loop to pass the id integer
        if batch[1].shape[-1] == 2:
            # 1D
            facet_tree = {"xmin": 0, "xmax": 1}
        elif batch[1].shape[-1] == 4:
            # 2D
            facet_tree = {"xmin": 0, "xmax": 1, "ymin": 2, "ymax": 3}
        else:
            raise ValueError("Other border batches are not implemented")
        b_losses_by_facet = jax.tree_util.tree_map(
            lambda c, f, fa, d: jnp.mean(
                _compute_boundary_loss(c, f, batch, u, params, fa, d)
            ),
            omega_boundary_condition,
            omega_boundary_fun,
            facet_tree,
            omega_boundary_dim,
        )  # when exploring leaves with None value (no condition) the returned
        # mse is None and we get rid of the None leaves of b_losses_by_facet
        # with the tree_leaves below
    else:
        facet_tuple = tuple(f for f in range(batch[1].shape[-1]))
        b_losses_by_facet = jax.tree_util.tree_map(
            lambda fa: jnp.mean(
                _compute_boundary_loss(
                    omega_boundary_condition,
                    omega_boundary_fun,
                    batch,
                    u,
                    params,
                    fa,
                    omega_boundary_dim,
                )
            ),
            facet_tuple,
        )
    mse_boundary_loss = jax.tree_util.tree_reduce(
        lambda x, y: x + y, jax.tree_util.tree_leaves(b_losses_by_facet)
    )
    return mse_boundary_loss


def observations_loss_apply(u, batches, params, vmap_axes, observed_values):
    # TODO implement for SPINN
    if isinstance(u, PINN):
        v_u = vmap(
            lambda *args: u(*args)[u.slice_solution],
            vmap_axes,
            0,
        )
        val = v_u(*batches, params)
        mse_observation_loss = jnp.mean(
            jnp.sum(
                (val - _check_user_func_return(observed_values, val.shape)) ** 2,
                # the reshape above avoids a potential missing (1,)
                axis=-1,
            )
        )
    elif isinstance(u, SPINN):
        raise RuntimeError("observation loss term not yet implemented for SPINNs")
    return mse_observation_loss


def initial_condition_apply(
    u, omega_batch, params, vmap_axes, initial_condition_fun, n
):
    if isinstance(u, PINN):
        v_u_t0 = vmap(
            lambda x, params: initial_condition_fun(x) - u(jnp.zeros((1,)), x, params),
            vmap_axes,
            0,
        )
        res = v_u_t0(omega_batch, params)  # NOTE take the tiled
        # omega_batch (ie omega_batch_) to have the same batch
        # dimension as params to be able to vmap.
        # Recall that by convention:
        # param_batch_dict = times_batch_size * omega_batch_size
        mse_initial_condition = jnp.mean(jnp.sum(res**2, axis=-1))
    elif isinstance(u, SPINN):
        values = lambda x: u(
            jnp.repeat(jnp.zeros((1, 1)), n, axis=0),
            x,
            params,
        )[0]
        omega_batch_grid = _get_grid(omega_batch)
        v_ini = values(omega_batch)
        ini = _check_user_func_return(
            initial_condition_fun(omega_batch_grid), v_ini.shape
        )
        res = ini - v_ini
        mse_initial_condition = jnp.mean(jnp.sum(res**2, axis=-1))
    return mse_initial_condition


def sobolev_reg_apply(u, batches, params, vmap_axes, sobolev_reg):
    # TODO implement for SPINN
    if isinstance(u, PINN):
        v_sob_reg = vmap(
            lambda *args: sobolev_reg(*args),  # pylint: disable=E1121
            vmap_axes,
            0,
        )
        mse_sobolev_loss = jnp.mean(v_sob_reg(*batches, params))
    elif isinstance(u, SPINN):
        raise RuntimeError("Sobolev loss term not yet implemented for SPINNs")
    return mse_sobolev_loss
