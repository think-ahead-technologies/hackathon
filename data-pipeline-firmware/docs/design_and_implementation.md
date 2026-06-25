[Click here](../README.md) to view the README.

## Design and implementation

The design of this application is minimalistic to get started with code examples on PSOC&trade; Edge MCU devices. All PSOC&trade; Edge E84 MCU applications have a dual-CPU three-project structure to develop code for the CM33 and CM55 cores. The CM33 core has two separate projects for the secure processing environment (SPE) and non-secure processing environment (NSPE). A project folder consists of various subfolders, each denoting a specific aspect of the project. The three project folders are as follows:

**Table 1. Application projects**

Project | Description
--------|------------------------
*proj_cm33_s* | Project for CM33 secure processing environment (SPE)
*proj_cm33_ns* | Project for CM33 non-secure processing environment (NSPE)
*proj_cm55* | CM55 project

<br>

In this code example, at device reset, the secure boot process starts from the ROM boot with the secure enclave (SE) as the root of trust (RoT). From the secure enclave, the boot flow is passed on to the system CPU subsystem where the secure CM33 application starts. After all necessary secure configurations, the flow is passed on to the non-secure CM33 application. 

Resource initialization for this example is performed by this CM33 non-secure project. It configures the system clocks, pins, clock to peripheral connections, and other platform resources. To conserve power, the CM33 CPU uses a multi-counter watchdog timer (MCWDT) 0 as a low-power timer (LPTIMER). This integration allows the FreeRTOS to enter a tickless idle state, enabling the device to transition into deep sleep when the CPU is idle, minimizing power consumption. It then enables the CM55 core using the `Cy_SysEnableCM55()` function. A FreeRTOS task `cm33_blinky_task` is created, which toggles the 'User LED1' every 1000 milliseconds. 

Once CM55 is enabled it configures system clocks, pins, clock to peripheral connections, and other platform resources. Subsequently, the MCWDT1 peripheral is initialized as an LPTIMER for FreeRTOS tickless idle mode. Finally, `cm55_blinky_task` is created which toggles the 'User LED2' at every 500 milliseconds.