"""
This script conducts a 2D simulation of flow around a cylinder using the lattice Boltzmann method (LBM). This is a classic problem in fluid dynamics and is often used to examine the behavior of fluid flow over a bluff body.

In this example you'll be introduced to the following concepts:

1. Lattice: A D2Q9 lattice is used, which is a two-dimensional lattice model with nine discrete velocity directions. This type of lattice allows for a precise representation of fluid flow in two dimensions.

2. Boundary Conditions: The script implements several types of boundary conditions:

    BounceBackHalfway: This condition is applied to the cylinder surface, simulating a no-slip condition where the fluid at the cylinder surface has zero velocity.
    ExtrapolationOutflow: This condition is applied at the outlet (right boundary), where the fluid is allowed to exit the simulation domain freely.
    Regularized: This condition is applied at the inlet (left boundary) and models the inflow of fluid into the domain with a specified velocity profile. Another Regularized condition is used for the stationary top and bottom walls.
3. Velocity Profile: The script uses a Poiseuille flow profile for the inlet velocity. This is a parabolic profile commonly seen in pipe flow.

4. Drag and lift calculation: The script computes the lift and drag on the cylinder, which are important quantities in fluid dynamics and aerodynamics.

5. Visualization: The simulation outputs data in VTK format for visualization. It also generates images of the velocity field. The data can be visualized using software like ParaView.

"""
import os
import json
import jax
from time import time
from jax import config
import numpy as np
import jax.numpy as jnp

from src.utils import *
from src.boundary_conditions import *
from src.models import BGKSim, KBCSim
from src.lattice import LatticeD2Q9

# Use 8 CPU devices
# os.environ["XLA_FLAGS"] = '--xla_force_host_platform_device_count=8'
jax.config.update('jax_enable_x64', True)

class Cylinder(BGKSim):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def set_boundary_conditions(self):
        # Define the cylinder surface
        coord = np.array([(i, j) for i in range(self.nx) for j in range(self.ny)])
        xx, yy = coord[:, 0], coord[:, 1]
        cx, cy = 2.*diam, 2.*diam
        cylinder = (xx - cx)**2 + (yy-cy)**2 <= (diam/2.)**2
        cylinder = coord[cylinder]
        implicit_distance = np.reshape((xx - cx)**2 + (yy-cy)**2 - (diam/2.)**2, (self.nx, self.ny))
        self.BCs.append(InterpolatedBounceBackBouzidi(tuple(cylinder.T), implicit_distance, self.gridInfo, self.precisionPolicy))

        # Outflow BC
        outlet = self.boundingBoxIndices['right']
        rho_outlet = np.ones(outlet.shape[0], dtype=self.precisionPolicy.compute_dtype)
        self.BCs.append(ExtrapolationOutflow(tuple(outlet.T), self.gridInfo, self.precisionPolicy))

        # Inlet BC
        inlet = self.boundingBoxIndices['left']
        rho_inlet = np.ones((inlet.shape[0], 1), dtype=self.precisionPolicy.compute_dtype)
        vel_inlet = np.zeros(inlet.shape, dtype=self.precisionPolicy.compute_dtype)
        yy_inlet = yy.reshape(self.nx, self.ny)[tuple(inlet.T)]
        vel_inlet[:, 0] = poiseuille_profile(yy_inlet,
                                             yy_inlet.min(),
                                             yy_inlet.max()-yy_inlet.min(), 3.0 / 2.0 * prescribed_vel)
        self.BCs.append(Regularized(tuple(inlet.T), self.gridInfo, self.precisionPolicy, 'velocity', vel_inlet))

        # No-slip BC for top and bottom
        wall = np.concatenate([self.boundingBoxIndices['top'], self.boundingBoxIndices['bottom']])
        vel_wall = np.zeros(wall.shape, dtype=self.precisionPolicy.compute_dtype)
        self.BCs.append(Regularized(tuple(wall.T), self.gridInfo, self.precisionPolicy, 'velocity', vel_wall))

    def output_data(self, **kwargs):
        # 1:-1 to remove boundary voxels (not needed for visualization when using bounce-back)
        rho = np.array(kwargs["rho"][..., 1:-1, :])
        u = np.array(kwargs["u"][..., 1:-1, :])
        timestep = kwargs["timestep"]
        u_prev = kwargs["u_prev"][..., 1:-1, :]

        if timestep == 0:
            self.CL_max = 0.0
            self.CD_max = 0.0
        if timestep > 0.8 * t_max:
            # compute lift and drag over the cyliner
            cylinder = self.BCs[0]
            boundary_force = cylinder.momentum_exchange_force(kwargs['f_poststreaming'], kwargs['f_postcollision'])
            boundary_force = np.sum(np.array(boundary_force), axis=0)
            drag = boundary_force[0]
            lift = boundary_force[1]
            cd = 2. * drag / (prescribed_vel ** 2 * diam)
            cl = 2. * lift / (prescribed_vel ** 2 * diam)

            u_old = np.linalg.norm(u_prev, axis=2)
            u_new = np.linalg.norm(u, axis=2)
            err = np.sum(np.abs(u_old - u_new))
            self.CL_max = max(self.CL_max, cl)
            self.CD_max = max(self.CD_max, cd)
            print('error= {:07.6f}, CL = {:07.6f}, CD = {:07.6f}'.format(err, cl, cd))
            save_image(timestep, u)

# Helper function to specify a parabolic poiseuille profile
poiseuille_profile  = lambda x,x0,d,umax: np.maximum(0.,4.*umax/(d**2)*((x-x0)*d-(x-x0)**2))

if __name__ == '__main__':
    precision = 'f64/f64'
    # diam_list = [10, 20, 30, 40, 60, 80]
    diam_list = [80]
    CL_list, CD_list = [], []
    result_dict = {}
    result_dict['resolution_list'] = diam_list
    for diam in diam_list:
        scale_factor = 80 / diam
        prescribed_vel = 0.003 * scale_factor
        lattice = LatticeD2Q9(precision)

        nx = int(22*diam)
        ny = int(4.1*diam)

        Re = 100.0
        visc = prescribed_vel * diam / Re
        omega = 1.0 / (3. * visc + 0.5)
        
        os.system('rm -rf ./*.vtk && rm -rf ./*.png')

        kwargs = {
            'lattice': lattice,
            'omega': omega,
            'nx': nx,
            'ny': ny,
            'nz': 0,
            'precision': precision,
            'io_rate': int(500 / scale_factor),
            'print_info_rate': int(10000 / scale_factor),
            'return_fpost': True    # Need to retain fpost-collision for computation of lift and drag
        }
        sim = Cylinder(**kwargs)
        t_max = int(1000000 / scale_factor)
        sim.run(t_max)
        CL_list.append(sim.CL_max)
        CD_list.append(sim.CD_max)

    result_dict['CL'] = CL_list
    result_dict['CD'] = CD_list
    with open('data.json', 'w') as fp:
        json.dump(result_dict, fp)
