#!/usr/bin/env python

import os
import numpy as np
from ase.io import read
import time

def find_fermi_level(band_energies, kpt_weight,
                     nelect, occ=None, sigma=0.01, nedos=100,
                     soc_band=False,
                     nmax=1000):
    '''
    Locate ther Fermi level from the band energies, k-points weights and number
    of electrons. 
                                             1.0
                Ne = \sum_{n,k} --------------------------- * w_k
                                  ((E_{nk}-E_f)/sigma) 
                                e                      + 1


    Inputs:
        band_energies: The band energies of shape (nspin, nkpts, nbnds)
        kpt_weight: The weight of each k-points.
        nelect: Number of electrons.
        occ:  1.0 for spin-polarized/SOC band energies, else 2.0.
        sigma: Broadening parameter for the Fermi-Dirac distribution.
        nedos: number of discrete points in approximately locating Fermi level.
        soc_band: band energies from SOC calculations?
        nmax: maximum iteration in finding the exact Fermi level.
    '''

    if band_energies.ndim == 2:
        band_energies = band_energies[None, :]

    nspin, nkpts, nbnds = band_energies.shape

    if occ is None:
        if nspin == 1 and (not soc_band):
            occ = 2.0
        else:
            occ = 1.0

    if nbnds > nedos:
        nedos = nbnds * 5

    kpt_weight = np.asarray(kpt_weight, dtype=float)
    assert kpt_weight.shape == (nkpts,)
    kpt_weight /= np.sum(kpt_weight)

    emin = band_energies.min()
    emax = band_energies.max()
    e0 = np.linspace(emin, emax, nedos)
    de = e0[1] - e0[0]

    # find the approximated Fermi level
    nelect_lt_en = np.array([
        np.sum(occ * (band_energies <= en) * kpt_weight[None, :, None])
        for en in e0
    ])
    ne_tmp = nelect_lt_en[nedos//2]
    if (np.abs(ne_tmp - nelect) < 0.05):
        i_fermi = nedos // 2
        i_lower = i_fermi - 1
        i_upper = i_fermi + 1
    elif (ne_tmp > nelect):
        for ii in range(nedos//2-1, -1, -1):
            ne_tmp = nelect_lt_en[ii]
            if ne_tmp < nelect:
                i_fermi = ii
                i_lower = i_fermi
                i_upper = i_fermi + 1
                break
    else:
        for ii in range(nedos//2+1, nedos):
            ne_tmp = nelect_lt_en[ii]
            if ne_tmp > nelect:
                i_fermi = ii
                i_lower = i_fermi - 1
                i_upper = i_fermi
                break

    ############################################################
    # Below is the algorithm used by VASP, much slower
    ############################################################
    # find the approximated Fermi level
    # x = (e0[None, None, None, :] - band_energies[:, :, :, None]) / sigma
    # x = x.clip(-100, 100)
    # dos = 1./sigma * np.exp(x) / (np.exp(x) + 1)**2 * \
    #       kpt_weight[None, :, None, None] * de
    # ddos = np.sum(dos, axis=(0,1,2))
    #
    # nelect_from_dos_int = np.sum(ddos[:nedos/2])
    # if (np.abs(nelect_from_dos_int - nelect) < 0.05):
    #     i_fermi = nedos / 2 - 1
    #     i_lower = i_fermi - 1
    #     i_upper = i_fermi + 1
    # elif (nelect_from_dos_int > nelect):
    #     for ii in range(nedos/2, -1, -1):
    #         nelect_from_dos_int = np.sum(ddos[:ii])
    #         if nelect_from_dos_int < nelect:
    #             i_fermi = ii
    #             i_lower = i_fermi
    #             i_upper = i_fermi + 1
    #             break
    # else:
    #     for ii in range(nedos/2, nedos):
    #         nelect_from_dos_int = np.sum(ddos[:ii])
    #         if nelect_from_dos_int > nelect:
    #             i_fermi = ii
    #             i_lower = i_fermi - 1
    #             i_upper = i_fermi
    #             break

    # Locate the exact Fermi level using bisectioning
    e_lower = e0[i_lower]
    e_upper = e0[i_upper]
    lower_B = False
    upper_B = False
    for ii in range(nmax):
        e_fermi = (e_lower + e_upper) / 2.

        z = (band_energies - e_fermi) / sigma
        z = z.clip(-100, 100)
        F_nk = occ / (np.exp(z) + 1)
        N = np.sum(F_nk * kpt_weight[None, :, None])
        # print ii, e_lower, e_upper, N

        if (np.abs(N - nelect) < 1E-10):
            break
        if (np.abs(e_upper - e_lower / (np.abs(e_fermi) + 1E-10)) < 1E-14):
            raise ValueError("Cannot reach the specified precision!")

        if (N > nelect):
            if not lower_B:
                e_lower -= de
            upper_B = True
            e_upper = e_fermi
        else:
            if not upper_B:
                e_upper += de
            lower_B = True
            e_lower = e_fermi

    if (ii == nmax - 1):
        raise ValueError("Cannot reach the specified precision!")

    return e_fermi, F_nk

def get_brillouin_zone_3d(cell):
    """
    Generate the Brillouin Zone of a given cell. The BZ is the Wigner-Seitz cell
    of the reciprocal lattice, which can be constructed by Voronoi decomposition
    to the reciprocal lattice.  A Voronoi diagram is a subdivision of the space
    into the nearest neighborhoods of a given set of points. 

    https://en.wikipedia.org/wiki/Wigner%E2%80%93Seitz_cell
    https://docs.scipy.org/doc/scipy/reference/tutorial/spatial.html#voronoi-diagrams
    """

    cell = np.asarray(cell, dtype=float)
    assert cell.shape == (3, 3)

    px, py, pz = np.tensordot(cell, np.mgrid[-1:2, -1:2, -1:2], axes=[0,0])
    points = np.c_[px.ravel(), py.ravel(), pz.ravel()]

    from scipy.spatial import Voronoi
    vor = Voronoi(points)
    
    bz_facets = []
    bz_ridges = []
    bz_vertices = []

    # for rid in vor.ridge_vertices:
    #     if( np.all(np.array(rid) >= 0) ):
    #         bz_ridges.append(vor.vertices[np.r_[rid, [rid[0]]]])
    #         bz_facets.append(vor.vertices[rid])

    for pid, rid in zip(vor.ridge_points, vor.ridge_vertices):
        # WHY 13 ????
        # The Voronoi ridges/facets are perpendicular to the lines drawn between the
        # input points. The 14th input point is [0, 0, 0].
        if( pid[0] == 13 or pid[1] == 13 ):
            bz_ridges.append(vor.vertices[np.r_[rid, [rid[0]]]])
            bz_facets.append(vor.vertices[rid])
            bz_vertices += rid
            
    bz_vertices = list(set(bz_vertices))

    return  vor.vertices[bz_vertices], bz_ridges, bz_facets
    
class ebands3d(object):
    '''
    '''
    
    def __init__(self, inf='EIGENVAL', efermi=None, kmesh=[], symprec=1E-5):
        '''
        Init
        '''

        self._fname = inf
        # the directory containing the input file
        self._dname  = os.path.dirname(inf)
        if self._dname == '':
            self._dname = '.'

        # read bands, k-points of the irreducible Brillouin Zone
        self.read_eigenval()
        # set the Fermi energy
        self.set_efermi(efermi)
        # set the k-points mesh
        self.set_kmesh(kmesh)
        # read POSCAR
        self.atoms = read(self._dname + "/POSCAR")
        # create the grid to ir map
        self.ir_kpts_map(symprec=symprec)

    def get_fermi_ebands3d(self):
        '''
        For those bands that corss the Fermi level, unfold the band energies on
        the irreducible BZ onto the whole reciprocal primitive cell.
        '''

        # band energies of the k-points within the primitive cell
        self.fermi_ebands3d_uc = []
        # band energies of the k-points within the Brillouin Zone
        self.fermi_ebands3d_bz = []

        for ispin in range(self.nspin):
            uc_tmp = []
            bz_tmp = []
            for iband in self.fermi_xbands[ispin]:
                # the band energies of the k-points within primitive cell
                etmp = self.ir_ebands[ispin, self.grid_to_ir_map, iband]
                etmp.shape = self.kmesh
                uc_tmp.append(etmp)

                # the band energies of the k-points within Brillouin Zone
                btmp = np.tile(etmp, (2,2,2))
                s = btmp.shape
                btmp.shape = (btmp.size)
                # set the band energies of the k-points outside BZ to a large
                # one so that the energy isosurface will not extent outside
                # beyond the BZ.
                btmp[np.logical_not(self.bz_in_kgrid_2uc)] = self.emax + 100.
                btmp.shape = s
                bz_tmp.append(btmp)

            self.fermi_ebands3d_uc.append(uc_tmp)
            self.fermi_ebands3d_bz.append(bz_tmp)

    def to_bxsf(self, prefix='ebands3d', ncol=6):
        '''
        Output the ebands3d as Xcrysden .bxsf format.
        '''

        with open(self._dname + '/{:s}.bxsf'.format(prefix), 'w') as out:
            out.write("BEGIN_INFO\n")
            out.write("  # Launch as: xcrysden --bxsf ebands3d.bxsf\n")
            out.write("  Fermi Energy: {:12.6f}\n".format(self.efermi))
            out.write("END_INFO\n\n")

            out.write("BEGIN_BLOCK_BANDGRID_3D\n")
            out.write("  band_energies\n")
            out.write("  BANDGRID_3D_BANDS\n")

            # the number of bands that corss the Fermi level
            number_fermi_xbands = sum([len(xx) for xx in self.fermi_xbands])
            out.write("    {:d}\n".format(number_fermi_xbands))
            # number of data-points in each direction (i.e. nx ny nz for 3D gr)
            out.write("    {:5d}{:5d}{:5d}\n".format(*(x for x in self.kmesh)))
            # origin of the bandgrid.
            # Warning: origin should be (0,0,0) (i.e. Gamma point)
            out.write("    {:16.8f}{:16.8f}{:16.8f}\n".format(0.0, 0.0, 0.0))
            # Reciprocal lattice vector
            out.write(
                    '\n'.join(["    " + ''.join(["%16.8f" % xx for xx in row])
                               for row in self.atoms.get_reciprocal_cell()])
                    )

            # write the band grid for each band, the values inside a band grid
            # are specified in row-major (i.e. C) order. This means that values
            # are written as:
            #
            # C-syntax:
            #   for (i=0; i<nx; i++)
            #     for (j=0; j<ny; j++)
            #       for (k=0; k<nz; k++)
            #         printf("%f",value[i][j][k]);
            #
            # FORTRAN syntax:
            #   write(*,*) (((value(ix,iy,iz),iz=1,nz),iy=1,ny),ix=1,nx)

            for ispin in range(self.nspin):
                sign = 1 if ispin == 0 else -1
                for ii in range(len(self.fermi_xbands[ispin])):
                    iband = self.fermi_xbands[ispin][ii]
                    b3d = self.fermi_ebands3d_uc[ispin][ii].copy()
                    nx, ny, nz = b3d.shape
                    b3d.shape = (nx * ny, nz)

                    out.write("\n    BAND: {:5d}\n".format(iband * sign))
                    out.write(
                            '\n'.join(["    " + ''.join(["%16.8e" % xx for xx in row])
                                       for row in b3d])
                            )
                    # np.savetxt(out, b3d, fmt='%16.8E')

            out.write("\n  END_BANDGRID_3D\n")
            out.write("END_BLOCK_BANDGRID_3D\n")

    def show_fermi_bz(self, plot='mpl',
                      savefig='fs.png',
                      cmap='Spectral'):
        '''
        Plotting the Fermi surface within the BZ using matplotlib.
        '''

        try:
            from skimage.measure import marching_cubes_lewiner as marching_cubes
        except ImportError:
            try:
                from skimage.measure import marching_cubes
            except ImportError:
                raise ImportError("scikit-image not installed.\n"
                    "Please install with it with `conda install scikit-image` or `pip install scikit-image`")

        bcell = self.atoms.get_reciprocal_cell()
        b1, b2, b3 = np.linalg.norm(bcell, axis=1)

        if plot.lower == 'mpl':
            ############################################################
            # Plot the Fermi surface using matplotlib
            ############################################################
            import matplotlib as mpl
            import matplotlib.pyplot as plt
            from mpl_toolkits.mplot3d import Axes3D
            from mpl_toolkits.mplot3d.art3d import Poly3DCollection

            ############################################################

            fig = plt.figure(figsize=(6,6))
            ax = fig.add_subplot(111, projection='3d')
            # ax.set_aspect('equal')
            ############################################################

            ############################################################
            # Plot the Brillouin Zone
            ############################################################
            p, l, f = get_brillouin_zone_3d(bcell)

            # The BZ outlines
            for xx in l:
                ax.plot(xx[:,0], xx[:,1], xx[:,2], color='k', lw=1.0)
            # art = Poly3DCollection(f, facecolor='k', alpha=0.1)
            # ax.add_collection3d(art)

            basis_vector_clrs = ['r', 'g', 'b']
            basis_vector_labs = ['x', 'y', 'z']
            for ii in range(3):
                ax.plot([0, bcell[ii,0]], [0, bcell[ii, 1]], [0, bcell[ii, 2]],
                        color=basis_vector_clrs[ii], lw=1.5)
                ax.text(bcell[ii, 0], bcell[ii, 1], bcell[ii, 2],
                        basis_vector_labs[ii])
            ############################################################
            # Plot the Fermi Surface.
            # Marching-cubes algorithm is used to find out the isosurface.
            ############################################################
            for ispin in range(self.nspin):
                for ii in range(len(self.fermi_xbands[ispin])):
                    b3d = self.fermi_ebands3d_bz[ispin][ii]
                    nx, ny, nz = b3d.shape

                    # https://scikit-image.org/docs/stable/api/skimage.measure.html#skimage.measure.marching_cubes_lewiner
                    verts, faces, normals, values = marching_cubes(b3d,
                            level=self.efermi,
                            spacing=(2*b1/nx, 2*b2/ny, 2*b3/nz)
                            )
                    verts_cart = np.dot(
                            verts / np.array([b1, b2, b3]) - np.ones(3),
                            bcell
                            )

                    cc = np.linalg.norm(np.sum(verts_cart[faces], axis=1), axis=1)
                    nn = mpl.colors.Normalize(vmin=cc.min(), vmax=cc.max())

                    art = Poly3DCollection(verts_cart[faces], facecolor='r',
                            alpha=0.8, color=mpl.cm.get_cmap(cmap)(nn(cc)))
                    # art.set_edgecolor('k')
                    ax.add_collection3d(art)

            ############################################################
            ax.set_xlim(-b1, b1)
            ax.set_ylim(-b2, b2)
            ax.set_zlim(-b3, b3)

            ax.set_title('Fermi Energy: {:.4f} eV'.format(self.efermi),
                         fontsize='small')

            # plt.tight_layout()
            plt.savefig(savefig, dpi=480)
            plt.show()
            ############################################################

        elif plot.lower() == 'mayavi':
            from mayavi import mlab 
            # from tvtk.tools import visual

            fig = mlab.figure()
            # visual.set_viewer(fig)

            # for b in bcell:
            #     x, y, z = b
            #     ar1 = visual.Arrow(x=y, y=y, z=z)
            #     arrow_length = np.linalg.norm(b)
            #     ar1.actor.scale=[arrow_length, arrow_length, arrow_length]
            #     ar1.pos = ar1.pos/arrow_length
            #     ar1.axis = [x, y, z]

            ############################################################
            # Plot the Brillouin Zone
            ############################################################
            p, l, f = get_brillouin_zone_3d(bcell)

            bz_line_width = b1 / 200
            # The BZ outlines
            for xx in l:
                mlab.plot3d(xx[:,0], xx[:,1], xx[:,2], 
                            tube_radius=bz_line_width,
                            color=(0, 0, 0))

            ############################################################
            # Plot the Fermi Surface.
            # Marching-cubes algorithm is used to find out the isosurface.
            ############################################################
            for ispin in range(self.nspin):
                for ii in range(len(self.fermi_xbands[ispin])):
                    b3d = self.fermi_ebands3d_bz[ispin][ii]
                    nx, ny, nz = b3d.shape
                    verts, faces, normals, values = marching_cubes(b3d,
                            level=self.efermi,
                            spacing=(2*b1/nx, 2*b2/ny, 2*b3/nz)
                            )
                    verts_cart = np.dot(
                            verts / np.array([b1, b2, b3]) - np.ones(3),
                            bcell
                            )
                    mlab.triangular_mesh([vert[0] for vert in verts_cart],
                                         [vert[1] for vert in verts_cart],
                                         [vert[2] for vert in verts_cart],
                                         faces,
                                         colormap='Spectral') 

            mlab.orientation_axes()
            mlab.savefig(savefig)
            mlab.show() 
        else:
            raise ValueError("Plotting method should be 'mpl' or 'mayavi'!")


    def ir_kpts_map(self, symprec=1E-5):
        '''
        Get irreducible k-points in BZ and the mapping between the mesh points
        and the ir kpoints.
        '''

        import spglib

        cell = (
                self.atoms.cell,
                self.atoms.get_scaled_positions(),
                self.atoms.get_atomic_numbers()
            )
        # mapping: a map between the grid points and ir k-points in grid
        mapping, grid = spglib.get_ir_reciprocal_mesh(self.kmesh, cell,
                is_shift=[0, 0, 0], symprec=symprec)

        # the k-point grid in the primitive cell
        # [-0.5, 0.5)
        self.kgrid_uc = grid
        ir_kpts = grid[np.unique(mapping)] / self.kmesh.astype(float)
        assert (ir_kpts.shape == self.ir_kpath.shape) and \
               (np.allclose(self.ir_kpath, ir_kpts)), \
               "Irreducible k-points generated by Spglib and VASP inconsistent!\n Try to reduce symprec, e.g. 1E-4."

        uniq_grid_index = np.unique(mapping)
        dump = np.zeros((grid.shape[0]), dtype=int)
        dump[uniq_grid_index] = np.arange(uniq_grid_index.size, dtype=int)
        # mapping between the grid points and the ir k-points
        self.grid_to_ir_map = dump[mapping]

        # t0 = time.time()

        # A stupid algorithm to find out the index of the k-points within the
        # BZ. First, expand the primitive cell from [0, 1] to [-1, 1]. Second,
        # find the k-points with BZ using quick nearest-neighbor lookup.
        nx, ny, nz = self.kmesh
        kx, ky, kz = np.mgrid[-nx:nx, -ny:ny, -nz:nz]
        self.kgrid_2uc = np.c_[kx.ravel(), ky.ravel(), kz.ravel()]

        # t1 = time.time()
        #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # https://docs.scipy.org/doc/scipy/reference/tutorial/spatial.html#voronoi-diagrams
        # cKDTree is implemented in cython, which is MUCH MUCH FASTER than KDTree
        #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        from scipy.spatial import cKDTree
        px, py, pz = np.tensordot(self.atoms.get_reciprocal_cell(), np.mgrid[-1:2, -1:2, -1:2], axes=[0,0])
        points = np.c_[px.ravel(), py.ravel(), pz.ravel()]
        tree = cKDTree(points)

        # t2 = time.time()

        #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # Gamma point belong to the first BZ.
        #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        gamma_region_id = tree.query([0,0,0])[1]
        kgrid_2uc_region_id = tree.query(np.dot(self.kgrid_2uc /
            np.array(self.kmesh, dtype=float),
            self.atoms.get_reciprocal_cell()))[1]
        #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

        # t3 = time.time()

        self.bz_in_kgrid_2uc = np.zeros(kgrid_2uc_region_id.size, dtype=bool)
        # find out the index of the k-points that are within BZ
        self.bz_in_kgrid_2uc[kgrid_2uc_region_id == gamma_region_id]  = True

        # Ideally, the number of k-points within BZ should be equal to the
        # number of k-points in the primitive cell.
        # print(np.sum(self.bz_in_kgrid_2uc), np.prod(self.kmesh))

        # t3 = time.time()
        # print("Time elapsed: {:.4f} {:.4f} {:.4f} s".format(t1 - t0, t2 - t1, t3 - t2))

    def set_kmesh(self, kmesh):
        '''
        Set the k-points mesh, Read from KPOINTS if not given.
        '''

        if kmesh:
            self.kmesh = kmesh
        else:
            with open(self._dname + "/KPOINTS") as k:
                dat = [l.split() for l in k if l.strip()]
                assert int(dat[1][0]) == 0, "Not automatic k-mesh generation in KPOINTS!"
                assert dat[2][0][0].upper() == 'G', "Please use Gamma center mesh!"
                self.kmesh = np.array([int(x) for x in dat[3]], dtype=int)
                self.kshift = np.array([float(x) for x in dat[4]], dtype=float)
                assert np.allclose(self.kshift, [0,0,0]), "K mesh shift should be 0!"

    def set_efermi(self, efermi):
        '''
        Set a new Fermi energy.
        '''

        if efermi is None:
            self.efermi, _ = find_fermi_level(self.ir_ebands, self.ir_kptwt, self.nelect)
        else:
            self.efermi = efermi
        self.find_fermicrossing_bands()

    def find_fermicrossing_bands(self):
        '''
        Find the index of the bands that cross the Fermi level.
        '''

        band_index = np.arange(self.nbnds, dtype=int)

        band_energy_max = np.max(self.ir_ebands, axis=1)
        band_energy_min = np.min(self.ir_ebands, axis=1)
        fermi_cross_band = (band_energy_min < self.efermi) & (self.efermi < band_energy_max)
        
        self.fermi_xbands = [band_index[fermi_cross_band[ii]] for ii in range(self.nspin)]
        if not np.all([True if x else False for x in self.fermi_xbands]):
            raise ValueError("No surface found at {:8.4f} eV!".format(self.efermi))
        
    def read_eigenval(self):
        '''
        Read band energies from VASP EIGENVAL file.
        '''

        with open(self._fname) as inp:
            # read all the data.
            dat = np.array([line.strip() for line in inp if line.strip()])

            # extract the needed info in the header.
            self.nspin = int(dat[0].split()[-1])
            self.nelect, self.ir_nkpts, self.nbnds = map(int, dat[5].split())

            # remove the header
            dat = dat[6:]

            # extract the k-points info
            dump = np.array([xx.split() for xx in dat[::self.nspin * self.nbnds + 1]], dtype=float)
            self.ir_kpath = dump[:self.ir_nkpts, :3]
            self.ir_kptwt = dump[:self.ir_nkpts, -1]

            # extract the ebands info
            ebands_flag = np.ones(dat.size, dtype=bool)
            ebands_flag[::self.nspin * self.nbnds + 1] = 0
            if self.nspin == 1:
                ebands = np.array([xx.split()[1] for xx in dat[ebands_flag]],
                        dtype=float)
            else:
                ebands = np.array([xx.split()[1:3] for xx in dat[ebands_flag]],
                        dtype=float)
            ebands.shape = (self.ir_nkpts, self.nspin, self.nbnds)
            self.ir_ebands = ebands.swapaxes(0, 1)

            self.emax = self.ir_ebands.max()
            self.emin = self.ir_ebands.min()

if __name__ == "__main__":
    """
    TEST
    """

    # cell = np.array([
    #     [np.sqrt(3)/2., 0.5, 0.0],
    #     [0.000000000,   1.0,  0.000000000],
    #     [0.000000000,   0.000000000,  1.0]])
    #
    # cell = np.sqrt(2) * np.array([
    #     [0.5, 0.5, 0.0],
    #     [0.0, 0.5, 0.5],
    #     [0.5, 0.0, 0.5]])
    #
    # # print(np.linalg.det(cell))
    # # cell = np.eye(3)
    #
    # p, l, f = get_brillouin_zone_3d(cell)
    #
    # import matplotlib.pyplot as plt
    # from mpl_toolkits.mplot3d import Axes3D
    # from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    #
    # fig = plt.figure()
    # ax = fig.add_subplot(111, projection='3d')
    # # ax.set_aspect('equal')
    #
    # for xx in l:
    #     ax.plot(xx[:,0], xx[:,1], xx[:,2], color='r', alpha=0.5, lw=1.0)
    #
    # art = Poly3DCollection(f, facecolor='k', alpha=0.1)
    # ax.add_collection3d(art)
    #
    # ax.set_xlim(-1,1)
    # ax.set_ylim(-1,1)
    # ax.set_zlim(-1,1)
    #
    # plt.show()

    # kpath, kptwt, ebands, nelect = read_eigenval()
    # efermi, fnk = find_fermi_level(ebands, kptwt, nelect, sigma=0.1)
    # print("Fermi Energy: {:8.4f} eV".format(efermi))

    xx = ebands3d()
    xx.get_fermi_ebands3d()
    xx.to_bxsf()
    xx.show_fermi_bz(plot='mayavi')
   
