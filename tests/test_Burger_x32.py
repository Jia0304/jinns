import pytest

import jax
import jax.numpy as jnp
from jax import random
import equinox as eqx
import optax
from jaxopt import OptaxSolver
import jinns


@pytest.fixture
def train_Burger_init():
    print(jax.devices())
    key = random.PRNGKey(2)
    eqx_list = [
        [eqx.nn.Linear, 2, 20],
        [jax.nn.tanh],
        [eqx.nn.Linear, 20, 20],
        [jax.nn.tanh],
        [eqx.nn.Linear, 20, 20],
        [jax.nn.tanh],
        [eqx.nn.Linear, 20, 1],
    ]
    key, subkey = random.split(key)
    init_param_fn, u = jinns.utils.create_PINN(subkey, eqx_list, "nonstatio_PDE", 1)

    init_nn_params = init_param_fn()

    n = 1000
    nt = 1000
    nb = 2
    omega_batch_size = 32
    temporal_batch_size = 20
    omega_border_batch_size = 1
    dim = 1
    xmin = -1
    xmax = 1
    tmin = 0
    tmax = 1
    Tmax = 1
    method = "uniform"

    train_data = jinns.data.CubicMeshPDENonStatio(
        subkey,
        n,
        nb,
        nt,
        omega_batch_size,
        omega_border_batch_size,
        temporal_batch_size,
        dim,
        (xmin,),
        (xmax,),
        tmin,
        tmax,
        method,
    )

    nu = 1 / (100 * jnp.pi)
    init_params = {"nn_params": init_nn_params, "eq_params": {"nu": nu}}

    def u0(x):
        return -jnp.sin(jnp.pi * x)

    be_loss = jinns.loss.BurgerEquation(Tmax=Tmax)

    loss_weights = {"dyn_loss": 1, "temporal_loss": 1, "boundary_loss": 0.75}

    loss = jinns.loss.LossPDENonStatio(
        u=u,
        loss_weights=loss_weights,
        dynamic_loss=be_loss,
        omega_boundary_fun=lambda dx: 0,
        omega_boundary_condition="dirichlet",
        temporal_boundary_fun=u0,
    )

    return init_params, loss, train_data


@pytest.fixture
def train_Burger_10it(train_Burger_init):
    """
    Fixture that requests a fixture
    """
    init_params, loss, train_data = train_Burger_init

    # NOTE we need to waste one get_batch() here to stay synchronized with the
    # notebook
    _ = loss.evaluate(init_params, train_data.get_batch())[0]

    params = init_params

    tx = optax.adam(learning_rate=1e-3)
    solver = OptaxSolver(
        opt=tx,
        fun=loss,
        has_aux=True,  # because the objective has aux output
        maxiter=500000,
    )
    n_iter = 10
    pinn_solver = jinns.solver.PinnSolver(optax_solver=solver, loss=loss, n_iter=n_iter)
    params, total_loss_list, loss_by_term_dict, _, _ = pinn_solver.solve(
        init_params=params, data=train_data
    )
    return total_loss_list[9]


def test_initial_loss_Burger(train_Burger_init):
    init_params, loss, train_data = train_Burger_init
    assert loss.evaluate(init_params, train_data.get_batch())[0] == 0.5594470500946045


def test_10it_Burger(train_Burger_10it):
    total_loss_val = train_Burger_10it
    assert total_loss_val == 0.43057507
